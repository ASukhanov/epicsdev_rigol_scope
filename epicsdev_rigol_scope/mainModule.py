"""Server of RIGOL oscilloscopes for EPICS PVAccess.
"""
# pylint: disable=invalid-name
__version__= 'v0.0.1 2026-01-13'# Created


import sys
#import os
import time
from time import perf_counter as timer
from collections import namedtuple
import threading
import re
import numpy as np

import pyvisa as visa

from p4p.nt import NTScalar, NTEnum
from p4p.nt.enum import ntenum
from p4p.server import Server
from p4p.server.thread import SharedPV

class C_():
    """Namespace for module properties"""
    AppName = 'epicsDevLecroyScope'
    verbose = 0
    pargs = None# Program arguments from main()
    cycle = 0
    lastRareUpdate = 0.
    server = None
    serverState = ''

    # Applications-specific constants
    scope = None
    PvDefs = []
    PVs = {}# dictionary of {pvName:PV}
    scpi = {}# {pvName:SCPI} map
    setterMap = {}
    readSettingQuery = None
    timeDelta = {}# execution times of different operations
    tstart = 0.
    exceptionCount = {}
    numacq = 0
    triggersLost = 0
    trigTime = 0
    previousScopeParametersQuery = ''
    channelsTriggered = []
    prevTscale = 0.
    xorigin = 0.
    xincrement = 0.
    npoints = 0.
    #previousPreamble =''
    ypars = None
    taxis = np.array([])

#Conversion map of python variables to EPICS types
EpicsType = {
    bool:   '?',
    str:    's',
    int:    'i',
    float:  'd',
    bytes:  'Y',
    type(None): 'V',
}
#``````````````````Constants``````````````````````````````````````````````````
Threadlock = threading.Lock()
OK = 0
IF_CHANGED =True
ElapsedTime = {}
BigEndian = False# Defined in configure_scope(WFMOUTPRE:BYT_Or LSB)
NDIVSX = 10# number of divisions
NDIVSY = 10#
#```````````````````Helper methods````````````````````````````````````````````
def printTime(): return time.strftime("%m%d:%H%M%S")
def printi(msg): print(f'inf_@{printTime()}: {msg}')
def printw(msg):
    txt = f'WAR_@{printTime()}: {msg}'
    print(txt)
    publish('status',txt)
def printe(msg):
    txt = f'ERR_{printTime()}: {msg}'
    print(txt)
    publish('status',txt)
def _printv(msg, level):
    if C_.verbose >= level: print(f'DBG{level}: {msg}')
def printv(msg): _printv(msg, 1)
def printvv(msg): _printv(msg, 2)
def printv3(msg): _printv(msg, 3)

def remove_firstWord(txt:str):
    """Remove first word from the string. Lecroy always send it."""
    return txt
    l = txt.split(' ',1)
    if len(l) > 1:
        return l[1]
    return l[0]

def pvobj(pvname:str):
    """Return PV named as pvname"""
    pvsEntry = C_.PVs[pvname]
    return next(iter(pvsEntry.values()))

def pvv(pvname:str):
    """Return PV value"""
    return pvobj(pvname).current()

def publish(pvname:str, value, ifChanged=False, t=None):
    """Post PV with new value"""
    try:
        pv = pvobj(pvname)
    except KeyError:
        printe(f'trying to publish wrong pv: {pvname}')
        sys.exit(1)
    if t is None:
        t = time.time()
    if not ifChanged or pv.current() != value:
        pv.post(value, timestamp=t)

def query(pvnames:list, explicitSCPIs=None):
    """Execute query request of the instrument for multiple PVs"""
    scpis = [C_.scpi[pvname] for pvname in pvnames]
    if explicitSCPIs:
        scpis += explicitSCPIs
    combinedScpi = '?;:'.join(scpis) + '?'
    try:
        with Threadlock:
            r = C_.scope.query(combinedScpi)
    except visa.errors.VisaIOError as e:
        printe(f'VisaIOError in query {combinedScpi}: {e}')
        return []
    return [remove_firstWord(i) for i in r.split(';')]

def configure_scope():
    """Send commands to configure data transfer"""
    #scope.write(":WAV:FORM BYTE") # Request 8-bit data
    with Threadlock:
        C_.scope.write(":WAV:FORM WORD;:MODE RAW")

def update_scopeParameters():
    """Update scope timing PVs"""
    with Threadlock:
        r = C_.scope.query(":WAV:XORigin?;:XINC?;POINts?;:CHAN1:DISP?;:CHAN2:DISP?;:CHAN3:DISP?;:CHAN4:DISP?")
    if r != (C_.previousScopeParametersQuery):
        printi(f'Scope parameters changed: {r}')
        l = r.split(';')
        C_.xorigin,C_.xincrement = float(l[0]), float(l[1])
        C_.npoints = int(l[2])
        taxis = np.arange(0, C_.npoints) * C_.xincrement + C_.xorigin
        publish('tAxis', taxis)
        publish('recLength', C_.npoints, IF_CHANGED)
        publish('timePerDiv', C_.npoints*C_.xincrement/NDIVSX, IF_CHANGED)
        publish('samplingRate', 1./C_.xincrement, IF_CHANGED)
        C_.channelsTriggered = []
        for ch in range(C_.pargs.channels):
            letter = l[ch+3]
            publish(f'c{ch+1}OnOff', letter, IF_CHANGED)
            if letter == '1':
                C_.channelsTriggered.append(ch+1)
    C_.previousScopeParametersQuery = r

#``````````````````Initialization and run``````````````````````````````````````
def start():
    """Start p4p server and run it until C_.serverState = Exited"""
    init()
    set_server('Start')

    # Loop
    C_.server = Server(providers=list(C_.PVs.values()))
    printi(f'Start server with polling interval {pvv("polling")}')
    while not C_.serverState.startswith('Exit'):
        time.sleep(pvv("polling"))
        if not C_.serverState.startswith('Stop'):
            poll()
    printi('Server is exited')

def init():
    """Module initialization"""
    init_visa()
    create_PVs()
    adopt_local_setting()

def poll():
    """Poll the instrument and process data"""
    C_.cycle += 1
    tnow = time.time()
    if tnow - C_.lastRareUpdate > 1.:
        C_.lastRareUpdate = tnow
        rareUpdate()

    if trigger_is_detected():
        acquire_waveforms()

    #print(f'poll {C_.cycle}')

def rareUpdate():
    """Called for infrequent updates"""
    #print(f'rareUpdate {time.time()}')
    #with Threadlock:
    #   r = query(['dateTime'])
    #if len(r) > 0:
    #    publish('dateTime', (r[0]))
    #LC#publish('actOnEvent', r[0], IF_CHANGED)
    update_scopeParameters()
    publish('scopeAcqCount', C_.numacq, IF_CHANGED)
    publish('lostTrigs', C_.triggersLost, IF_CHANGED)
    #print(f'ElapsedTime: {ElapsedTime}')
    if str(pvv('trigState')).startswith('STOP'):
        printe('Acquisition is stopped')
    publish('timing', [(round(-i,6)) for i in ElapsedTime.values()])
#,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,
#``````````````````Initialization functions```````````````````````````````````
def create_PVs():
    """Create PVs from PvDefs"""
    #``````````````````Definition of PVs``````````````````````````````````````
    R,W = False,True # values for the 'writable' field 
    # abbreviations of EPICS Value fields
    U,CL,CH = 'display.units','control.limitLow','control.limitHigh'
    # abbreviations of extra fields
    LV,SCPI,SET = 'legalValues', 'SCPI', 'setter'
    PvDef = namedtuple('PvDef',
[   'name',     'desc',                             'value', 'writable', 'fields', 'extra'], defaults=[False,{},{}])
    C_.PvDefs = [

# Mandatory PVs
PvDef('version',    'Program version',              __version__),
PvDef('status',     'Server status',                ''),
PvDef('server',     'Server control',               'Stop', W, {},
    {LV:'Start,Stop,Clear,Exit,Started,Stopped,Exited',SET:set_server}),
PvDef('polling',    'Polling interval',              1.0, W, {U:'S'}),

# instruments's PVs
PvDef('host',       'IP_address',                   C_.pargs.addr, R),
PvDef('instrCtrl',  'Scope control commands',       '*OPC', W, {},
    {LV:'*OPC,*OPC?,*CLS?,*RST,!d,*TST,*IDN?,ACQuire:STATE?,AUTOset EXECute'}),
PvDef('instrCmdS',  'Execute a scope command',      '*IDN?', W, {},
    {SET:set_instrCmdS}),
PvDef('instrCmdR',  'Response of the instrCmdS',    ''),
PvDef('debug',      'Set verposity level',          0,W,{},
    {SET: set_debug}),
PvDef('timing',     'Timing: [trigger,waveforms,preamble,query,publish]', [0.], R, {U:'S'}),

# scope-specific PVs
PvDef('dateTime',   'Scope`s date & time',          ''),#,R,{},{SCPI:'DATE'}),
PvDef('acqCount',   'Number of acquisition recorded', 0),
PvDef('scopeAcqCount',  'Acquisition count of the scope', 0),# R, {},{SCPI:'ACQuire:NUMACq'}),
PvDef('recLength',  'Number of points per waveform', 1000, W, {},
    {CL: 100, CH:1000000}),# SCPI:'HORizontal:RECOrdlength'}),
PvDef('samplingRate', 'Sampling Rate',              0., W, {U:'Hz'}),#,{SCPI:'HORizontal:SAMPLERate'}),
PvDef('timePerDiv', 'Horizontal scale (1/{NDIVSX} of full scale)', 2.e-6, R, {U:'S'},
    {SCPI: 'TIMebase:SCALe'}),
PvDef('tAxis',      'Array of horizontal axis',     [0.], R, {U:'S'}),
PvDef('date',       'Scope date time',              '?'),
PvDef('lostTrigs',  'Number of triggers lost',      0),
PvDef('trigger',    'Click to force trigger event to occur', 'Trigger',W,{},
    {LV:'Force!,Trigger'}),# SET:set_trigger}),
PvDef('trigSource', 'Trigger source',               'CH1', W, {},
    {LV:'CH1,CH2,CH3,CH4,CH5,CH6,CH7,CH8,LINE,AUX'}),# SCPI:'TRIGger:A:EDGE:SOUrce'}),
PvDef('trigLevel',   'Trigger level',               0., W, {U:'V'},
    {SET:set_trigLevel}),
PvDef('trigDelay',   'Trigger delay',               0., W, {U:'S'}),
    #{SCPI:'HORizontal:DELay:MODe ON;:HORizontal:DELay:TIMe'}),
PvDef('trigHoldoff', 'Time after trigger when it will not accept another triggers',
    5.0E-3, W, {U:'S'}),# {SCPI:'TRIGger:A:HOLDoff:TIMe'}),
PvDef('trigSlope',  'Trigger slope',                'RISE', W, {},
    {LV:'RISE,FALL,EITHER'}),# SCPI:'TRIGger:A:EDGE:SLOpe'}),
PvDef('trigCoupling',   'Trigger coupling',         'DC', W, {},
    {LV:'DC,HFRej,LFRej,NoiseRej'}),# SCPI:'TRIGger:A:EDGE:COUPling'}),
PvDef('trigMode',   'Trigger mode. Should be NORM.', 'NORM', W, {},
    {LV:'NORM,AUTO,SING', SCPI:'TRIGger:SWEep'}),
PvDef('trigState',  'State of the triggering system, Should be: READY', '?', R, {}),
    #{SCPI:'TRIGger:STATE'}),
PvDef('actOnEvent', 'Enables the saving waveforms on trigger', 0, W, {},
    {CL: 0, CH:1}),# SCPI:'ACTONEVent:ENable'}),
PvDef('aOE_Limit',  'Limit of Action On Event saves',   80, W, {}),
    #{SCPI:'ACTONEVent:LIMITCount'}),
PvDef('setup', 'Save/recall instrument state',      'Setup', W, {},
    {SET:set_setup, LV:'Save,Recall,Setup'}),
    ]
    # Templates for channel-related PVs
    ChannelTemplates = [
PvDef('c$VoltsPerDiv', 'Vertical sensitivity',      0., W, {U:'V/du'}),
    #{SCPI:'CH$:SCAle'}),
PvDef('c$Position',    'Vertical position',         0., W, {U:'du'}),
    #{SCPI:'CH$:OFFSet 0;POSition'}),
PvDef('c$Coupling',    'Coupling',                  'DC', W, {}),
    #{LV:'AC,DC', SCPI:'CH$:COUPling'}),
PvDef('c$Termination', 'Termination',               '50.000', W, {U:'Ohm'}),
    #{LV:'50.000,1.0000E+6', SCPI:'CH$:TER'}),
PvDef('c$OnOff',   'Trace On/Off',                  '0', W, {}),
    #{LV:'0,1', SCPI:'DISplay:WAVEView1:CH$:STATE'}),
PvDef('c$Waveform', 'Channel data',                  [0.], R),
PvDef('c$Peak2Peak',   'Peak to peak amplitude',    0., R, {U:'du'}),
    ]
    # extend PvDefs with channel-related PVs
    for pvdef in ChannelTemplates:
        for ch in range(C_.pargs.channels):
            newname = pvdef.name.replace('$',str(ch+1))
            fields = pvdef
            newpvdef = PvDef(newname, *fields[1:])
            C_.PvDefs.append(newpvdef)

    # Create PVs from updated PvDefs
    for pvdef in C_.PvDefs:
        count = 1
        if isinstance(pvdef.value, str):
            first = pvdef.value
        else:
            try:
                first = pvdef.value[0]
                count = len(pvdef.value)
                if count == 1:
                    count = 0# variable array
            except TypeError:
                first = pvdef.value
                count = 1
        ptype = EpicsType[type(first)]
        if count != 1:
            ptype = 'a'+ptype 
        printvv(f'>creating {pvdef.name} of type {ptype}, v:{pvdef.value}')
        ts = time.time()

        # handle the field 'extra'
        if len(pvdef.extra) == 0:
            normativeType = NTScalar(ptype, display=True, control=pvdef.writable)
            value = pvdef.value
        else:
            # handle legalValues
            lv = pvdef.extra.get(LV)
            if lv is None:
                normativeType = NTScalar(ptype, display=True, control=pvdef.writable)
                value = pvdef.value
            else:
                lv = lv.split(',')
                try:
                    idx = lv.index(pvdef.value)
                except ValueError:
                    printe(f'Could not create PV for {pvdef.name}: its value {pvdef.value} is not in legalValues')
                    sys.exit(1)
                normativeType = NTEnum(control=pvdef.writable)
                value = {'choices': lv, 'index': idx}
                printvv(f'LegalValues of {pvdef.name}: {lv}, value: {value}')

        # create PV
        try:
            pv = SharedPV(nt=normativeType)
            pv.open(value)
            if isinstance(normativeType,NTEnum):
                pv.post(value, timestamp=ts)
            else:
                V = pv._wrap(value, timestamp=ts)
                V['display.description'] = pvdef.desc
                for k,v in pvdef.fields.items():
                    V[k] = v
                pv.post(V)
        except Exception as e:
            printw(f'Could not create PV for {pvdef.name}: {str(e)[:200]}')
            continue
        pv.name = C_.pargs.prefix + pvdef.name

        # for writables we need to add setters
        if pvdef.writable:
            @pv.put
            def handle(pv, op):
                ct = time.time()
                v = op.value()
                vr = v.raw.value
                printvv(f'v type: {type(v)} = {v}, {vr}')
                if isinstance(v, ntenum):
                    vr = v
                corename = pv.name.removeprefix(C_.pargs.prefix)

                printv(f'setting {corename} to {vr}')
                # execute SCPI command for corresponding PVs
                scpi = C_.scpi.get(corename)
                setter = C_.setterMap.get(corename)
                if setter: # it is higher priority
                    printv(f'>setter[{setter}]')
                    setter(vr)
                    # value could change by the setter
                    if corename not in ['instrCmdS']:
                        printv(f'update vr of {corename}: {vr}')
                        vr = pvv(corename)
                elif scpi:
                    printv(f'>scopeCmd({scpi})')
                    scopeCmd(f'{scpi} {vr}')

                pv.post(vr, timestamp=ct) # update subscribers
                op.done()

        if C_.pargs.listPVs:
            printi(f'PV {pv.name} created: {pv}')
        C_.PVs[pvdef.name] = {pv.name:pv}

    # Make a map of pvNames to SCPI commands and a combined query for all SCPI-related parameters
    printv('>make_par2scpiMap and setterMap')
    for pvdef in C_.PvDefs:
        scpi = pvdef.extra.get('SCPI')
        if scpi:
            scpi = scpi.replace('$',pvdef.name[1])
            # remove lower case letter for brevity
            scpi = ''.join([char for char in scpi if not char.islower()])
            # check, if scpi is correct:
            if C_.verbose > 1:
                s = scpi+'?'
                with Threadlock:
                    r = C_.scope.query(s)
                printvv(f'>query {s}: {r}')
            if not scpi[0] in '!*':
                C_.scpi[pvdef.name] = scpi
        setter = pvdef.extra.get('setter')
        if setter:
            C_.setterMap[pvdef.name] = setter
    # add special case of TrigLevel
    #C_.scpi['trigLevel'] = trigLevelCmd()

    C_.readSettingQuery = '?;'.join(C_.scpi.values()) + '?'
    printv(f'setterMap: {C_.setterMap}')
    #printv(f'readSettingQuery:\n{C_.readSettingQuery}')

def init_visa():
    '''Init VISA interface to device'''
    try:
        C_.rm = visa.ResourceManager('@py')
    except ModuleNotFoundError as e:
        printe(f'in visa.ResourceManager: {e}')
        sys.exit(1)

    #rn = C_.pargs.addr+':1861'.replace(':','::')
    rn = C_.pargs.addr.replace(':','::')
    resourceName = 'TCPIP::'+rn+'::INSTR'# if SOCET then port is needed
    try:
        C_.scope = C_.rm.open_resource(resourceName)
    except visa.errors.VisaIOError as e:
        printe(f'Could not open instrument at {C_.pargs.addr}')
        sys.exit(1)

    C_.scope.set_visa_attribute( visa.constants.VI_ATTR_TERMCHAR_EN, True)
    C_.scope.timeout = 2000 # ms
    try:
        C_.scope.write('*CLS') # clear ESR, previous error messages will be cleared
    except visa.errors.VisaIOError as e:
        printe(f'Resource {resourceName} not responding: {e}')
        sys.exit()
    resetNeeded = False
    try:  
        C_.scope.write('*OPC')# that does not work!
        printi('*OPC?'+C_.scope.query('*OPC?'))
        printi('*ESR?'+C_.scope.query('*ESR?'))
    except visa.errors.VisaIOError as e:    
        printw('*OPC?,*ESR? failed');
        resetNeeded = True

    if resetNeeded:
        printw('>resetNeeded')
        #LC#C_.scope.write('!d') 
        sys.exit(1)

    idn = C_.scope.query('*IDN?')
    print(f"IDN: {idn}")
    if not idn.startswith('RIGOL'):
        print('ERROR: instrument is not RIGOL')
        sys.exit(1)

    C_.scope.encoding = 'latin_1'
    C_.scope.read_termination = '\n'#Important!

    configure_scope()

# def close_visa(C_):
    # C_.rm.close()
    # C_.scope = None

#``````````````````Setters
def set_debug(level):
    printi(f'Setting verbosity level to {level}')
    C_.verbose = level

def set_instrCmdS(cmd):
    """Setter for the instrCmdS PV"""
    return scopeCmd(cmd, True)

def set_server(state=None):
    """setter for the server PV"""
    #printv(f'>set_server({state}), {type(state)}')
    if state is None:
        state = pvv('server')
        printi(f'Setting server state to {state}')
    state = str(state)
    if state == 'Start':
        printi('starting the server')
        configure_scope()
        adopt_local_setting()
        with Threadlock:
            C_.scope.write(':RUN')
        publish('server','Started')
    elif state == 'Stop':
        printi('server stopped')
        publish('server','Stopped')
    elif state == 'Exit':
        printi('server is exiting')
        publish('server','Exited')
    elif state == 'Clear':
        publish('acqCount', 0)
        #publish('lostTrigs', 0)
        C_.triggersLost = 0
        publish('status','Cleared')
        # set server to previous state
        set_server(C_.serverState)
    C_.serverState = state
    return OK

def set_setup(action):
    """setter for the setup PV"""
    action = str(action)
    with Threadlock:
        if action == 'Save':
            C_.scope.write("SAVE:SETUP 'c:/latest.set'")
        elif action == 'Recall':
            if str(pvv('server')).startswith('Start'):
                printw('Please set server to Stop before Recalling')
                publish('setup','Setup')
                return
            C_.scope.write("RECAll:SETUp 'c:/latest.set'")
        elif action != 'Setup':
            printw(f'WAR: wrong setup action: {action}')
    if action == 'Recall':
        adopt_local_setting()
    publish('setup','Setup')

def set_trigger(action):
    return #LC#
    action = str(action)
    if action.startswith('Force'):
        C_.scope.write('TRIGger FORCe')
    publish('trigger','Trigger')

def set_trigLevel(value):
    printv(f'set_trigLevel: {type(value),value}')
    return #LC#
    with Threadlock:
        C_.scope.write(trigLevelCmd() + f' {value}')
        v = C_.scope.query(trigLevelCmd() + '?')
    publish('trigLevel',v)

#``````````````````````````````````````````````````````````````````````````````
def scopeCmd(cmd, updateCmdM=False):
    """send command to scope, update instrCmdR if needed, return 0 if OK"""
    printv(f'>scopeCmd: {cmd, updateCmdM} @{round(time.time(),6)}')
    rc = 0
    try:
        with Threadlock:
            if cmd[-1] == '?':
                reply = C_.scope.query(cmd)
                print(f'scope reply:{reply}')
                if updateCmdM:
                    printv('updating instrCmdR')
                    publish('instrCmdR',remove_firstWord(reply))
            else:
                C_.scope.write(cmd)
    except:
        return handle_exception('in scopeCmd(%s)'%cmd)
    printv(f'<scopeCmd {rc}')
    return rc

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
    printv('>adopt_local_setting')
    ct = time.time()
    nothingChanged = True
    try:
        with Threadlock:
            values = C_.scope.query(C_.readSettingQuery).split(';')
        printvv(f'parnames: {C_.scpi.keys()}')
        printvv(f'C_.readSettingQuery: {C_.readSettingQuery}')
        printvv(f'values: {values}')
        if len(C_.scpi) != len(values):
            l = min(len(C_.scpi),len(values))
            printe(f'ReadSetting failed for {list(C_.scpi.keys())[l]}')
            #sys.exit(1)
            return
        for parname,v in zip(C_.scpi, values):
            pv = pvobj(parname)
            pvValue = pv.current()
            v = remove_firstWord(v)
            v = v.split(' ',1)[0]

            if isinstance(pvValue, ntenum):
                pvValue = str(pvValue)
            else:
                v = type(pvValue.raw.value)(v)
            printv(f'parname,v: {parname, type(v), v, type(pvValue), pvValue}')
            valueChanged = pvValue != v
            if valueChanged:
                printv(f'posting {pv.name}={v}')
                pv.post(v, timestamp=ct)
                nothingChanged = False
                printv(f'PV {pv.name} changed using local value {v}')

    except visa.errors.VisaIOError as e:
        printe(f'VisaIOError in adopt_local_setting: {e}, SCPI:{C_.readSettingQuery}')
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
    ElapsedTime['trigger_detection'] = ts - timer()
    printv(f'Trigger detected {C_.numacq}')
    return True

def acquire_waveforms():
    """acquire scope waveforms"""
    printv(f'>acquire_waveform for channels {C_.channelsTriggered}')
    if not C_.pargs.waveforms:
        return
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
                #preamble = C_.scope.query(f'DATA:SOUrce {ch};:WFMOutpre?')
                C_.scope.write(f':STOP;:WAV:SOURce CHANnel{ch}')
                #r =  C_.scope.query(':WAV:YINC?;:WAV:YREFerence?;WAV:YORigin?')
                preamble =  C_.scope.query(':WAV:PRE?')
            dt = timer() - ts
            #printvv(f'aw preamble{ch}: {preamble}, dt: {ch}: {dt}')
            ElapsedTime['preamble'] -= dt
            # if preamble did not change, then we can skip its decoding, we can save ~65us
            preamble = preamble.split(',')
            ypars = tuple([float(i) for i in preamble[7:]])
            yincr, yorig, yref = ypars
            #if ypars != C_.ypars:
            #    msg = f'vertical scaling changed: {ypars}'
            #    C_.ypars = ypars
            #    printi(msg)
            #    publish('status',msg)
            publish(f'c{ch}VoltsPerDiv', yincr, IF_CHANGED, 
                            t=C_.trigtime)
            publish(f'c{ch}Position', yorig, IF_CHANGED, 
                            t=C_.trigtime)

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
            publish(f'c{ch}Waveform', v, t=C_.trigTime)
            publish(f'c{ch}Peak2Peak',
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
#,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,
