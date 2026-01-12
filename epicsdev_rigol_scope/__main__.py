"""Server of RIGOL oscilloscopes for EPICS PVAccess"""
import argparse
from . import  mainModule

def main():
    """parse common arguments and start the server"""
    parser = argparse.ArgumentParser(description = __doc__,
      formatter_class=argparse.ArgumentDefaultsHelpFormatter,
      epilog=f'{mainModule.C_.AppName, mainModule.__version__}')
    parser.add_argument('-a','--addr', default= '192.168.27.31', help=
'Intstrument address:port')    
    parser.add_argument('-C','--channels', type=int, default=4, help=
'Number of channels at the scope')
    parser.add_argument('-l', '--listPVs', action='store_true', help=
'List all generated PVs')
    parser.add_argument('-p', '--prefix', default='Rigol:', help=
'Prefix, to be applied to all PV names')
    parser.add_argument('-v', '--verbose', action='count', default=0, help=
'Show more log messages (-vv: show even more)')
    parser.add_argument('-w', '--waveforms', action='store_false', help=
'Disable acquisition of waveforms')
    pargs = parser.parse_args()

    print(f'pargs: {pargs}')
    mainModule.C_.verbose = pargs.verbose #verbose is needed for printv 
    mainModule.C_.pargs = pargs

    mainModule.start()

if __name__ == "__main__":
    main()
