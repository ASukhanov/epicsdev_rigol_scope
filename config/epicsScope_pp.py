"""Pypet page for oscilloscopes served by epicsdev-based server."""
# pylint: disable=invalid-name
__version__ = 'v1.3.2 2026-02-01'
print(f'epicsScope {__version__}')

#``````````````````Definitions````````````````````````````````````````````````
# python expressions and functions, used in the spreadsheet
_ = ''
def span(x,y=1): return {'span':[x,y]}
def color(*v): return {'color':v[0]} if len(v)==1 else {'color':list(v)}
def font(size): return {'font':['Arial',size]}
def just(i): return {'justify':{0:'left',1:'center',2:'right'}[i]}
def slider(minValue,maxValue):
    """Definition of the GUI element: horizontal slider with flexible range"""
    return {'widget':'hslider','opLimits':[minValue,maxValue],'span':[2,1]}

LargeFont = {'color':'light gray', **font(18), 'fgColor':'dark green'}
ButtonFont = {'font':['Open Sans Extrabold',14]}# Comic Sans MS
# Attributes for gray row, it should be in the first cell:
#GrayRow = {'ATTRIBUTES':{'color':'light gray', **font(12)}}
LYRow = {'ATTRIBUTES':{'color':'light yellow'}}
lColor = color('lightGreen')

# definition for plotting cell
PyPath = 'python -m'
PaneT = 'timing[1] timing[3]'
#``````````````````PyPage Object``````````````````````````````````````````````
class PyPage():
    """Pypet page for oscilloscopes served by epicsDevScope* server"""
    def __init__(self, instance:str, title:str, channels=4):
        """instance: unique name of the page.
        For EPICS it is usually device prefix 
        """
        print(f'Instantiating Page {title} for device{instance} with {channels} channels')

        #``````````Mandatory class members starts here````````````````````````
        self.namespace = 'PVA'
        self.title = title

        #``````````Page attributes, optional`````````````````````````
        self.page = {**color(240,240,240)}# Does not work
        #self.page['editable'] = False

        #``````````Definition of columns`````````````````````````````
        self.columns = {
            1: {'width': 120, 'justify': 'right'},
            2: {'width': 80},
            3: {'width': 80},
            4: {'width': 80},
            5: {'width': 80},
            6: {'width': 80},
            7: {'width': 80},
            8: {'width': 80},
            9: {'width': 80},
        }
        """`````````````````Configuration of rows`````````````````````````````
A row is a list of comma-separated cell definitions.
The cell definition is one of the following: 
  1)string, 2)device:parameters, 3)dictionary.
The dictionary is used when the cell requires extra features like color, width,
description etc. The dictionary is single-entry {key:value}, where the key is a 
string or device:parameter and the value is dictionary of the features.
        """
        D = instance

        #``````````Abbreviations, used in cell definitions
        def ChLine(suffix):
            return [f'{D}c{ch+1:02}{suffix}' for ch in range(channels)]
        #FOption = ' -file '+logreqMap.get(D,'')
        host = '130.199.41.111'
        #scopeWWW = {'WWW':{'launch':f'firefox http://{host}/Tektronix/#/client/c/   Tek%20e*Scope',
        #    **lColor, **ButtonFont, **span(1,2)}}
        PaneP2P = ' '.join([f'c{i+1:02}Peak2Peak' for i in range(channels)])
        PaneWF = ' '.join([f'c{i+1:02}Waveform' for i in range(channels)])
        Plot = {'Plot':{'launch':f'{PyPath} pvplot -aV:{instance} -#0"{PaneP2P}" -#1"{PaneWF}" -#2"{PaneT}"',
            **lColor, **ButtonFont}}
        print(f'Plot command: {Plot}')

        #``````````mandatory member```````````````````````````````````````````
        self.rows = [
#['Device:', D, {D+'version':span(2,1)},_, 'scope time:', {D+'dateTime':span(2,1)}],
#['State:', D+'server', 'Recall:', D+'setup',_,'verbose:',D+'verbose'],
#['Status:', {D+'status': span(6,1)}],
#['Polling Interval:', D+'polling',_,_,_,Plot,_],#scopeWWW],
['Device:',D, D+'server', D+'version', 'host:',D+'host',_],
['Status:', {D+'status': span(8,1)}],
['Cycle time:',D+'cycleTime', 'Sleep:',D+'sleep', 'Cycle:',D+'cycle', Plot],
#'', {D+'ReadSetting':
#{**color('cyan'),**span(2,1)}},_,_,],
['Triggers recorded:', D+'acqCount', 'Lost:', D+'lostTrigs',
  'Acquisitions:',D+'scopeAcqCount',_], 
['Time/Div:', {D+'timePerDiv':span(2,1)},_,'recLength:', D+'recLengthS',
  D+'recLengthR',_],
['SamplingRate:', {D+'samplingRate':span(2,1)},_,_,_,_,_],
#['Trigger:', D+'trigSourceS', D+'trigCouplingS', D+'trigSlopeS', 'level:', D+'trigLevelS', 'delay:', {D+'trigDelay':span(2,1)},''],
['Trigger state:',D+'trigState','   trigMode:',D+'trigMode',
  'TrigLevel','TrigDelay',_],
[{D+'trigger':color('lightCyan')}, D+'trigSource', D+'trigCoupling',
  D+'trigSlope', D+'trigLevel', D+'trigDelay',_],
[{'ATTRIBUTES':color('lightGreen')}, 'Channels:','CH1','CH2','CH3','CH4','CH5','CH6'],
['Volt/Div:']+ChLine('VoltsPerDiv'),
['Offset:']+ChLine('VoltOffset'),
['Coupling:']+ChLine('Coupling'),
['Termination:']+ChLine('Termination'),
['On/Off:']+ChLine('OnOff'),
#['Delay:']+ChLine('DelayFromTriggerM'),
#['Waveform:']+ChLine('WaveforM'),
['Peak2Peak:']+ChLine('Peak2Peak'),
#[''],
# ["Trigger",D+'trigSourceS',D+'trigLevelS',D+'trigSlopeS',D+'trigModeS'],
# ['',"Setup"],
# ["Repair:",D+'updateDataA',D+'deviceClearA',D+'resetScopeA',D+'forceTrigA'],
# ["Session",D+'SaveSession',D+'RecallSession',"folder:",D+'folderS'],
# [D+'currentSessionS',"<-current",D+'nextSessionS',"out off",D+'sessionsM'],
#[{'ATTRIBUTES':{'color':'yellow'}},
#['tAxis:',D+'tAxis'],
[LYRow,'',{'For Experts only!':{**span(6,1),**font(14)}}],
[LYRow,'Scope command:', {D+'instrCmdS':span(2,1)},_,{D+'instrCmdR':span(4,1)}],
[LYRow,'Special commands', {D+'instrCtrl':span(2,1)},_,_,_,_,_,],
[LYRow,'Timing:',{D+'timing':span(6,1)}],
#[LYRow,'ActOnEvent',D+'actOnEvent','AOE_Limit',D+'aOE_Limit',_,_,_],
]

