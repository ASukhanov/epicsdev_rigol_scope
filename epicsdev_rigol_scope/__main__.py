"""Simulated multi-channel ADC device server using epicsdev module."""
# pylint: disable=invalid-name
__version__= 'v2.1.0 26-02-01'# updated for epicsdev v2.1.0
#TODO: Stop acquisition once for all channels, not per channel.

import sys
import time
from time import perf_counter as timer
import argparse
import threading
import numpy as np

import pyvisa as visa
from pyvisa.errors import VisaIOError

from epicsdev.epicsdev import  Server, SPV, init_epicsdev, sleep,\
    serverState, set_server, publish, pvobj, pvv,\
    printi, printe, printw, printv, printvv

#``````````````````PVs defined here```````````````````````````````````````````
def myPVDefs():
    """PV definitions"""
    SET,U,LL,LH,SCPI = 'setter','units','limitLow','limitHigh','scpi'
    alarm = {'valueAlarm':{'lowAlarmLimit':-9., 'highAlarmLimit':9.}}
    pvDefs = [
# instruments's PVs
['setup', 'Save/recall instrument state to/from latest or operational setup',
    SPV(['Setup','Save latest','Save oper','Recall latest','Recall oper'],'WD'),
    {SET:set_setup}],
['visaResource', 'VISA resource to access the device', SPV(pargs.resource,'R'), {}],
['dateTime',    'Scope`s date & time', SPV('N/A'), {}],
['acqCount',    'Number of acquisition recorded', SPV(0), {}],
['scopeAcqCount',  'Acquisition count of the scope', SPV(0), {}],# N/A for RIGOL
['lostTrigs',   'Number of triggers lost',  SPV(0), {}],
['instrCtrl',   'Scope control commands',
    SPV('*IDN?,*RST,*CLS,*ESR?,*OPC?,*STB?'.split(','),'WD'), {}],
['instrCmdS',   'Execute a scope command. Features: RWE',  SPV('*IDN?','W'), {
    SET:set_instrCmdS}],
['instrCmdR',   'Response of the instrCmdS',  SPV(''), {}],
#``````````````````Horizontal PVs
['recLengthS',   'Number of points per waveform',
    SPV(['AUTO','1k','10k','100k','1M','5M','10M','25M','50M'],'WD'), {
    SET:set_recLengthS}],
['recLengthR',   'Number of points per waveform read', SPV(0.), {
    SCPI:'ACQuire:MDEPth'}],
['samplingRate', 'Sampling Rate',  SPV(0.), {U:'Hz',
    SCPI:'ACQuire:SRATe'}],
['timePerDiv', f'Horizontal scale (1/{NDIVSX} of full scale)', SPV(2.e-6,'W'), {U:'S/du',
    SCPI: 'TIMebase:SCALe', SET:set_scpi}],
['tAxis',       'Horizontal axis array', SPV([0.]), {U:'S'}],

#``````````````````Trigger PVs
['trigger',     'Click to force trigger event to occur',
    SPV(['Trigger','Force!'],'WD'), {SET:set_trigger}],
['trigType',   'Trigger ', SPV(['EDGE','PULS','SLOP','VID'],'WD'),
    #PATTern|DURation|TIMeout|RUNT|WINDow|DELay|SETup|NEDGe|RS232|IIC|SPI|CAN|LIN
    {SCPI:'TRIGger:MODE',SET:set_scpi}],
['trigCoupling',   'Trigger coupling', SPV(['DC','AC','LFR','HFR'],'D'),
    {SCPI:'TRIGger:COUPling'}],
['trigState',   'Current trigger status: TD,WAIT,RUN,AUTO and STOP', SPV('?'),
    {SCPI:'TRIGger:STATus'}],
['trigMode',   'Trigger mode', SPV(['NORM','AUTO','SING'],'WD'),
    {SCPI:'TRIGger:SWEep',SET:set_scpi}],
['trigDelay',   'Trigger position', SPV(0.), {U:'S',
    SCPI:'TRIGger:POSition'}],
['trigSource', 'Trigger source',
    SPV('CHAN1,CHAN2,CHAN3,CHAN4,EXT,D0,D1,D2,D3,D4,D5,D6,D7'.split(','),'WD'),
    {SCPI:'TRIGger:EDGE:SOURce',SET:set_scpi}],
['trigSlope',  'Trigger slope', SPV(['POS','NEG','RFALI'],'WD'),
    {SCPI:'TRIGger:EDGE:SLOPe',SET:set_scpi}],
['trigLevel', 'Trigger level', SPV(0.,'W'), {U:'V', 
    SCPI:'TRIGger:EDGE:LEVel',SET:set_scpi}],
#``````````````````Auxiliary PVs
['timing',  'Performance timing', SPV([0.]), {U:'S'}],
    ]

    #``````````````Templates for channel-related PVs.
    # The <n> in the name will be replaced with channel number.
    # Important: SPV cannot be used in this list!
    ChannelTemplates = [
['c<n>OnOff', 'Enable/disable channel', (['1','0'],'WD'), 
    {SET:set_scpi, SCPI:'CHANnel<n>:DISPlay'}],
['c<n>Coupling', 'Channel coupling', (['DC','AC','GND'],'WD'), 
    {SCPI:'CHANnel<n>:COUPling'}],
['c<n>VoltsPerDiv',  'Vertical scale',  (1E-3,'W'), {U:'V/du',
    SCPI:'CHANnel<n>:SCALe', LL:500E-6, LH:10.}],
['c<n>VoltOffset',  'Vertical offset',  (0.,), {U:'V',
    SCPI:'CHANnel<n>:OFFSet'}],
['c<n>Termination', 'Input termination', ('1M','R'), {U:'Ohm'}],# fixed in RIGOL
['c<n>Waveform', 'Waveform array',           ([0.],), {U:'du'}],
['c<n>Mean',     'Mean of the waveform',     (0.,'A'), {U:'du'}],
['c<n>Peak2Peak','Peak-to-peak amplitude',   (0.,'A'), {U:'du',**alarm}],
    ]
    # extend PvDefs with channel-related PVs
    for ch in range(pargs.channels):
        for pvdef in ChannelTemplates:
            newpvdef = pvdef.copy()
            newpvdef[0] = pvdef[0].replace('<n>',f'{ch+1:02}')
            newpvdef[2] = SPV(*pvdef[2])
            pvDefs.append(newpvdef)
    return pvDefs
#,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,
#``````````````````Constants
Threadlock = threading.Lock()
OK = 0
NotOK = -1
IF_CHANGED =True
ElapsedTime = {}
NDIVSX = 10# number of vertical divisions of the scope display
NDIVSY = 10#
#,,,,,,,,,,,,,,,,,,
class C_():
    """Namespace for module properties"""
    scope = None
    scpi = {}# {pvName:SCPI} map
    setterMap = {}
    PvDefs = []
    readSettingQuery = None
    lastRareUpdate = 0
    exceptionCount = {}
    numacq = 0
    triggersLost = 0
    trigTime = 0
    previousScopeParametersQuery = ''
    channelsTriggered = []
    prevTscale = 0.
    xorigin = 0.
    xincrement = 0.
    npoints = 0
    ypars = None
#``````````````````Setters````````````````````````````````````````````````````
def scopeCmd(cmd):
    """Send command to scope, return reply if any."""
    printv(f'>scopeCmd: {cmd}')
    reply = None
    try:
        if cmd[-1] == '?':
            with Threadlock:
                reply = C_.scope.query(cmd)
        else:
            with Threadlock:
                C_.scope.write(cmd)
    except:
        handle_exception(f'in scopeCmd{cmd}')
    return reply

def set_instrCmdS(cmd, *_):
    """Setter for the instrCmdS PV"""
    publish('instrCmdR','')
    reply = scopeCmd(cmd)
    if reply is not None:
        publish('instrCmdR',reply)
    publish('instrCmdS',cmd)

def serverStateChanged(newState:str):
    """Start device function called when server is started"""
    if newState == 'Start':
        printi('start_device called')
        configure_scope()
        adopt_local_setting()
    elif newState == 'Stop':
        printi('stop_device called')
    elif newState == 'Clear':
        printi('clear_device called')

def set_setup(action_slot, *_):
    """setter for the setup PV"""
    if action_slot == 'Setup':
        return
    action,slot = str(action_slot).split()
    fileName = {'latest':'C:/latest.stp','oper':'C:/operational.stp'}[slot]
    #print(f'set_setup: {action} {fileName}')
    if action == 'Save':
        status = f'Setup was saved to {fileName}'
        with Threadlock:
            C_.scope.write(f'SAVE:SETup {fileName}')
    elif action == 'Recall':
        status = f'Setup was recalled from {fileName}'
        if str(pvv('server')).startswith('Start'):
            printw('Please set server to Stop before Recalling')
            publish('setup','Setup')
            return NotOK
        with Threadlock:
            C_.scope.write(f"LOAD:SETUp {fileName}")
    publish('setup','Setup')
    publish('status', status)
    if action == 'Recall':
        adopt_local_setting()

def set_trigger(value, *_):
    """setter for the trigger PV"""
    printv(f'set_trigger: {value}')
    if str(value) == 'Force!':
        with Threadlock:
            C_.scope.write('TFORce')
        publish('trigger','Trigger')

def set_recLengthS(value, *_):
    """setter for the recLengthS PV"""
    printv(f'set_recLengthS: {value}')
    with Threadlock:
        C_.scope.write(f'ACQuire:MDEPth {value}')
    publish('recLengthS', value)

def set_scpi(value, pv, *_):
    """setter for SCPI-associated PVs"""
    print(f'set_scpi({value},{pv.name})')
    scpi = C_.scpi.get(pv.name,None)
    if scpi is None:
        printe(f'No SCPI defined for PV {pv.name}')
        return
    scpi = scpi.replace('<n>',pv.name[2])# replace <n> with channel number
    print(f'set_scpi: {scpi} {value}')
    scpi += f' {value}' if pv.writable else '?'
    printv(f'set_scpi command: {scpi}')
    reply = scopeCmd(scpi)
    if reply is not None:
        publish(pv.name, reply)
    publish(pv.name, value)

#``````````````````Instrument communication functions`````````````````````````
def query(pvnames, explicitSCPIs=None):
    """Execute query request of the instrument for multiple PVs"""
    scpis = [C_.scpi[pvname] for pvname in pvnames]
    if explicitSCPIs:
        scpis += explicitSCPIs
    combinedScpi = '?;:'.join(scpis) + '?'
    print(f'combinedScpi: {combinedScpi}')
    with Threadlock:
        r = C_.scope.query(combinedScpi)
    return r.split(';')

def configure_scope():
    """Send commands to configure data transfer"""
    printi('configure_scope')
    with Threadlock:
        C_.scope.write(":WAV:FORM WORD;:MODE RAW;:SAVE:OVERlap ON")

def update_scopeParameters():
    """Update scope timing PVs"""
    with Threadlock:
        xscpi = (":WAV:XORigin?;:XINC?;POINts?;:CHAN1:DISP?;"
                 ":CHAN2:DISP?;:CHAN3:DISP?;:CHAN4:DISP?;:TRIG:EDGE:LEV?")
        r = C_.scope.query(xscpi)
    if r != (C_.previousScopeParametersQuery):
        printi(f'Scope parameters changed: {r}')
        l = r.split(';')
        C_.xorigin,C_.xincrement = float(l[0]), float(l[1])
        C_.npoints = int(l[2])
        taxis = np.arange(0, C_.npoints) * C_.xincrement + C_.xorigin
        publish('tAxis', taxis)
        publish('recLengthR', C_.npoints, IF_CHANGED)
        publish('timePerDiv', C_.npoints*C_.xincrement/NDIVSX, IF_CHANGED)
        publish('samplingRate', 1./C_.xincrement, IF_CHANGED)
        C_.channelsTriggered = []
        for ch in range(pargs.channels):
            letter = l[ch+3]
            publish(f'c{ch+1:02}OnOff', letter, IF_CHANGED)
            if letter == '1':
                C_.channelsTriggered.append(ch+1)
        publish('trigLevel', float(l[7]), IF_CHANGED)
    C_.previousScopeParametersQuery = r

def init_visa():
    '''Init VISA interface to device'''
    try:
        rm = visa.ResourceManager('@py')
    except ModuleNotFoundError as e:
        printe(f'in visa.ResourceManager: {e}')
        sys.exit(1)
    # # Check if instrument is on netwok
    #ipAddress = repr(pvv('address')).replace('\'','')
    # if os.system(f'ping -c 1 -W 1 {ipAddress}') != 0:
    #     printe(f'IP address {ipAddress} is not pingable')
    #     sys.exit(1)

    resourceName = pargs.resource
    printv(f'Opening resource {resourceName}')
    try:
        C_.scope = rm.open_resource(resourceName)
    except visa.errors.VisaIOError as e:
        printe(f'Could not open resource {resourceName}: {e}')
        sys.exit(1)
    C_.scope.set_visa_attribute( visa.constants.VI_ATTR_TERMCHAR_EN, True)
    C_.scope.timeout = 2000 # ms
    try:
        C_.scope.write('*CLS') # clear ESR, previous error messages will be cleared
    except Exception as e:
        printe(f'Resource {resourceName} not responding: {e}')
        sys.exit()
    C_.scope.write('*OPC')# that does not work!
    resetNeeded = False
    try:    printi('*OPC?'+C_.scope.query('*OPC?'))
    except: 
        printw('*OPC? failed'); resetNeeded = True
    try:    printi('*ESR?'+C_.scope.query('*ESR?'))
    except: 
        printw('*ESR? failed'); resetNeeded = True

    if resetNeeded:
        printi('Resetting instrument to factory defaults')
        C_.scope.write('*RST')
        sys.exit(1)

    idn = C_.scope.query('*IDN?')
    print(f"IDN: {idn}")
    if not idn.startswith('RIGOL'):
        print('ERROR: instrument is not RIGOL')
        sys.exit(1)

    C_.scope.encoding = 'latin_1'
    C_.scope.read_termination = '\n'#Important.

# def close_visa(C_):
    # rm.close()
    # C_.scope = None
#``````````````````````````````````````````````````````````````````````````````
def handle_exception(where):
    """Handle exception"""
    #print('handle_exception',sys.exc_info())
    exceptionText = str(sys.exc_info()[1])
    tokens = exceptionText.split()
    msg = 'ERR:'+tokens[0] if tokens[0] == 'VI_ERROR_TMO' else exceptionText
    msg = msg+': '+where
    printe(msg)
    with Threadlock:
        C_.scope.write('*CLS')
    return -1

def adopt_local_setting():
    """Read scope setting and update PVs"""
    printi('adopt_local_setting')
    ct = time.time()
    nothingChanged = True
    try:
        printvv(f'readSettingQuery: {C_.readSettingQuery}')
        with Threadlock:
            values = C_.scope.query(C_.readSettingQuery).split(';')
        printvv(f'parnames: {C_.scpi.keys()}')
        printvv(f'C_.readSettingQuery: {C_.readSettingQuery}')
        printvv(f'values: {values}')
        if len(C_.scpi) != len(values):
            l = min(len(C_.scpi),len(values))
            printe(f'ReadSetting failed for {list(C_.scpi.keys())[l]}')
            sys.exit(1)
        for parname,v in zip(C_.scpi, values):
            pv = pvobj(parname)
            pvValue = pv.current()
            if pv.discrete:
                pvValue = str(pvValue)
            else:
                try:
                    v = type(pvValue.raw.value)(v)
                except ValueError:
                    printe(f'ValueError converting {v} to {type(pvValue.raw.value)} for PV {parname}')
                    sys.exit(1)
            #printv(f'parname,v: {parname, type(v), v, type(pvValue), pvValue}')
            valueChanged = pvValue != v
            if valueChanged:
                printv(f'posting {pv.name}={v}')
                pv.post(v, timestamp=ct)
                nothingChanged = False

    except visa.errors.VisaIOError as e:
        printe('VisaIOError in adopt_local_setting:'+str(e))
    if nothingChanged:
        printi('Local setting did not change.')

def trigLevelCmd():
    """Generate SCPI command for trigger level control"""
    ch = str(pvv('trigSource'))
    if ch[:2] != 'CH':
        return ''
    r = 'TRIGger:A:LEVel:'+ch
    printv(f'tlcmd: {r}')
    return r
#,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,
#``````````````````Acquisition-related functions``````````````````````````````
def trigger_is_detected():
    """check if scope was triggered"""
    ts = timer()
    try:
        with Threadlock:
            r = C_.scope.query(':TRIGger:STATus?')
    except visa.errors.VisaIOError as e:
        printe(f'Exception in query for trigger: {e}')
        for exc in C_.exceptionCount:
            if exc in str(e):
                C_.exceptionCount[exc] += 1
                errCountLimit = 2
                if C_.exceptionCount[exc] >= errCountLimit:
                    printe(f'Processing stopped due to {exc} happened {errCountLimit} times')
                    set_server('Exit')
                else:
                    printw(f'Exception  #{C_.exceptionCount[exc]} during processing: {exc}')
        return False

    # last query was successfull, clear error counts
    for i in C_.exceptionCount:
        C_.exceptionCount[i] = 0
    publish('trigState', r, IF_CHANGED)

    if not r.startswith('TD'):
        return False

    # trigger detected
    C_.numacq += 1
    C_.trigtime = time.time()
    ElapsedTime['trigger_detection'] = round(ts - timer(),6)
    printv(f'Trigger detected {C_.numacq}')
    return True

#``````````````````Acquisition-related functions``````````````````````````````
def acquire_waveforms():
    """Acquire waveforms from the device and publish them."""
    printv(f'>acquire_waveform for channels {C_.channelsTriggered}')
    publish('acqCount', pvv('acqCount') + 1, t=C_.trigTime)
    ElapsedTime['acquire_wf'] = timer()
    ElapsedTime['preamble'] = 0.
    ElapsedTime['query_wf'] = 0.
    ElapsedTime['publish_wf'] = 0.
    for ch in C_.channelsTriggered:
        # refresh scalings
        ts = timer()
        operation = 'getting preamble'
        try:
            # most of the time is spent here, 4 times longer than the reading of waveform:
            with Threadlock:
                # stop acquisition to read preamble and waveform,
                # because they may change during acquisition
                C_.scope.write(f':STOP;:WAV:SOURce CHANnel{ch}')
                #r =  C_.scope.query(':WAV:YINC?;:WAV:YREFerence?;WAV:YORigin?')
                preamble =  C_.scope.query(':WAV:PRE?')
            dt = timer() - ts
            printvv(f'aw preamble{ch}: {preamble}, dt: {ch}: {dt}')
            ElapsedTime['preamble'] -= dt
            # if preamble did not change, then we can skip its decoding, we can save ~65us
            preamble = preamble.split(',')
            ypars = tuple([float(i) for i in preamble[7:]])
            yincr, yorig, yref = ypars
            # if ypars != C_.ypars:
            #     msg = f'vertical scaling changed: {ypars}'
            #     C_.ypars = ypars
            #     printi(msg)
            #     publish('status',msg)
            #publish(f'c{ch:02}VoltsPerDiv', yincr, IF_CHANGED, 
            #                t=C_.trigtime)
            #publish(f'c{ch:02}VoltOffset', yorig, IF_CHANGED, 
            #                t=C_.trigtime)

            # acquire the waveform
            ts = timer()
            operation = 'getting waveform'
            with Threadlock:
                waveform = C_.scope.query_binary_values(":WAV:DATA?",
                    datatype='H', container=np.array)
                C_.scope.write(':RUN')
            ElapsedTime['query_wf'] -= timer() - ts
            v = (waveform - yorig - yref) * yincr

            # publish
            ts = timer()
            operation = 'publishing'
            publish(f'c{ch:02}Waveform', v, t=C_.trigTime)
            publish(f'c{ch:02}Peak2Peak',
                (v.max() - v.min()),
                t = C_.trigtime)
        except visa.errors.VisaIOError as e:
            printe(f'Visa exception in {operation} for {ch}:{e}')
            break
        except Exception as e:
            printe(f'Exception in processing channel {ch}: {e}')

        ElapsedTime['publish_wf'] -= timer() - ts
    ElapsedTime['acquire_wf'] -= timer()
    printvv(f'elapsedTime: {ElapsedTime}')

def make_readSettingQuery():
    """Create combined SCPI query to read all settings at once"""
    for pvdef in C_.PvDefs:
        pvname = pvdef[0]
        # if setter is defined, add it to the setterMap
        setter = pvdef[3].get('setter',None)
        if setter is not None:
            C_.setterMap[pvname] = setter
        # if SCPI is defined, add it to the readSettingQuery
        scpi = pvdef[3].get('scpi',None)
        if scpi is None:
            continue
        scpi = scpi.replace('<n>',pvname[2])#
        scpi = ''.join([char for char in scpi if not char.islower()])# remove lowercase letters
        # check if scpi is correct:
        s = scpi+'?'
        try:
            with Threadlock:
                r = C_.scope.query(s)
        except VisaIOError as e:
            printe(f'Invalid SCPI in PV {pvname}: {scpi}? : {e}')
            sys.exit(1)
        printvv(f'SCPI for PV {pvname}: {scpi}, reply: {r}')
        if not scpi[0] in '!*':# only SCPI starting with !,* are not added
            C_.scpi[pvname] = scpi
        
    C_.readSettingQuery = '?;'.join(C_.scpi.values()) + '?'
    printv(f'readSettingQuery: {C_.readSettingQuery}')
    printv(f'setterMap: {C_.setterMap}')

def init():
    """Module initialization"""
    init_visa()
    make_readSettingQuery()
    adopt_local_setting()

def rareUpdate():
    """Called for infrequent updates"""
    update_scopeParameters()
    #publish('scopeAcqCount', C_.numacq, IF_CHANGED)
    publish('lostTrigs', C_.triggersLost, IF_CHANGED)
    ##print(f'ElapsedTime: {ElapsedTime}')
    if str(pvv('trigState')).startswith('STOP'):
        printe('Acquisition is stopped')
    publish('timing', [(round(-i,6)) for i in ElapsedTime.values()])

def poll():
    """Example of polling function"""
    tnow = time.time()
    if tnow - C_.lastRareUpdate > 1.:
        C_.lastRareUpdate = tnow
        rareUpdate()

    if trigger_is_detected():
        acquire_waveforms()

#``````````````````Main```````````````````````````````````````````````````````
if __name__ == "__main__":
    # Argument parsing
    parser = argparse.ArgumentParser(description = __doc__,
    formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    epilog=f'{__version__}')
    parser.add_argument('-c', '--channels', type=int, default=4, help=
    'Number of channels per device')
    parser.add_argument('-d', '--device', default='rigol', help=
    'Device name, the PV name will be <device><index>:')
    parser.add_argument('-i', '--index', default='0', help=
    'Device index, the PV name will be <device><index>:') 
    parser.add_argument('-r', '--resource', default='TCPIP::192.168.27.31::INSTR', help=
    'Resource string to access the device')
    parser.add_argument('-v', '--verbose', action='count', default=0, help=
    'Show more log messages (-vv: show even more)') 
    pargs = parser.parse_args()
    print(f'pargs: {pargs}')

    # Initialize epicsdev and PVs
    pargs.prefix = f'{pargs.device}{pargs.index}:'
    C_.PvDefs = myPVDefs()
    PVs = init_epicsdev(pargs.prefix, C_.PvDefs, pargs.verbose, serverStateChanged)

    # Initialize the device, using pargs if needed.
    # That can be used to set the number of points in the waveform, for example.
    init()

    # Start the Server. Use your set_server, if needed.
    set_server('Start')

    # Main loop
    server = Server(providers=[PVs])
    printi(f'Server for {pargs.prefix} started. Sleeping per cycle: {repr(pvv("sleep"))} S.')
    while True:
        state = serverState()
        if state.startswith('Exit'):
            break
        if not state.startswith('Stop'):
            poll()
        sleep()
    printi('Server is exited')
