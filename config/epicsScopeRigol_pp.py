"""Pypet definition for RIGOL epicsScope"""
import epicsScope_pp as module

def PyPage(**_):
    return  module.PyPage(instance='Rigol:', title='RIGOL DHO924',
        channels=4)
