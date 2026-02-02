# epicsdev_rigol_scope.
Python-based EPICS PVAccess server for RIGOL oscilloscopes.
It is based on [p4p](https://epics-base.github.io/p4p/) and [epicsdev](https://github.com/ASukhanov/epicsdev) packages 
and it can run standalone on Linux, OSX, and Windows platforms.<br>
It was tested with RIGOL DHO924 on linux.

## Installation
```pip install epicsdev_rigol_scope```<br>
For control GUI and plotting:
```pip install pypeto,pvplot```

## Run
To start: ```python -m epicsdev_rigol_scope -r'TCPIP::192.168.27.31::INSTR'```<br>
Control GUI:<br>
```python -m pypeto -c path_to_repository/config -f epicsdev_rigol_scope```<br>

![Control page](docs/pypet.jpg), ![Plots](docs/pvplot.jpg)
