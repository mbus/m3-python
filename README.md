M3-Python
=========

[![Build Status](https://travis-ci.org/mbus/m3-python.svg?branch=master)](https://travis-ci.org/mbus/m3-python)

Utilities and software libraries for the [M3 ecosystem](http://cubeworks.us) and interfacing with the [ICE board](http://mbus.io/ice.html). 

```bash
$ pip install m3
$ # Program the board via the optical interface:
$ m3_ice goc flash program.bin
```

The `m3_ice` utility should handle most use cases, however users are free to write their scripts against the `ice` library directly.
Developers are encouraged to consider some of the higher-level interfaces provided by `m3_common`.

m3_ice
------

Command-line tool for programming and debugging M3 chips.

```
usage: m3_ice [-h] [-s SERIAL] [-w] [-y]
              {reset,hardreset,power,snoop,ein,goc} ...

Tool to control the ICE board and attached M3 chips.

optional arguments:
  -h, --help            show this help message and exit
  -s SERIAL, --serial SERIAL
                        Path to ICE serial device (default: None)
  -w, --wait-for-messages
                        Wait for messages (hang) when done. (default: False)
  -y, --yes             Use default values for all prompts. (default: False)

Commands:
  Actions supported by the ICE board

  {reset,hardreset,power,snoop,ein,goc}
    reset               Cycle 0.6V rail to reset M3 chips
    hardreset           Cycle all power rails to cold-boot M3 chips
    power               Control power rails sent to connected M3 chips
    snoop               Passively monitor MBus messages
    ein                 Command the chip via the EIN protocol
    goc                 Send commands via the GOC protocol (blinking light)
```


m3_ice_simulator
----------------

Simulate an ICE hardware board. This is useful primarily for testing scripts and unit testing the ICE library itself.

```
$ m3_ice_simulator -h
usage: m3_ice_simulator [-h] [-i ICE_VERSION] [-s SERIAL] [-S]
                        [--i2c-mask I2C_MASK] [-a] [-g] [-r REPLAY]

optional arguments:
  -h, --help            show this help message and exit
  -i ICE_VERSION, --ice-version ICE_VERSION
                        Maximum ICE Version to emulate (1, 2, or 3)
  -s SERIAL, --serial SERIAL
                        Serial port to connect to
  -S, --suppress-fake-serial
                        Do not create a software serial tunnerl
  --i2c-mask I2C_MASK   Address mask for fake_ice i2c address
  -a, --ack-all         Only supports i2c at the moment
  -g, --generate-messages
                        Generate periodic, random MBus messages
  -r REPLAY, --replay REPLAY
                        Replay a ICE snoop trace
```
