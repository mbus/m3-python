#!/usr/bin/env python

import datetime
import os
import sys
import struct
import socket
import tempfile
import time

import logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger('program')

import threading
import subprocess

import inspect

from m3.ice import ICE
import m3.ice_simulator
import m3.m3_ice

class TestTransactions(object):
    class TestFailedException(Exception):
        #logger.info('='*42 + '\nTEST FAILED\n' + '='*42)
        pass

    @classmethod
    def setup_class(cls, ):
       
        #make sure we start with a fresh serial port
        serial_port=m3.ice_simulator._FAKE_SERIAL_SIMULATOR_ENDPOINT
        if os.path.exists(serial_port):
            os.remove(serial_port)

        cls.sim_thread = None
        filedir = os.path.dirname(os.path.realpath(__file__))

        os.environ['ICE_NOSLEEP'] = '1'
        sim_args = m3.ice_simulator.Simulator.get_parser().parse_args([
                                        #'-s ' + serial_port, 
                                        '-t ' + filedir +'/transactions/timeout.trx'])

        cls.sim_thread = threading.Thread(
                target=m3.ice_simulator.Simulator(args=sim_args).run,
                name='fake_ice',
                )
        cls.sim_thread.daemon = True
        cls.sim_thread.start()

        time.sleep(0.5)

        #cls.ice = ICE()
        #cls.ice.connect(com2)
        #cls.ice.connect(serial_port)

    @classmethod
    def teardown_class(cls):
        m3.ice_simulator.destroy_fake_serial()

    def test_timeout(self):
        logger.info("Testing Timeout Feature") 

        serial_port=m3.ice_simulator._FAKE_SERIAL_CONNECTTO_ENDPOINT
        print ('Using ' + str(serial_port))
        self.driver = m3.m3_ice.m3_ice(['--debug',
                                    '-s '+ serial_port,
                                    'snoop'])
        self.driver.cmd_snoop()
        
        
        # I have no idea how this works.....



if __name__ == '__main__':
    if len(sys.argv) not in (2,):
        logger.info("USAGE: %s SERIAL_DEVICE\n" % (sys.argv[0]))
        sys.exit(2)

    i = ICETests(serial_port=sys.argv[1])

    logger.info('')
    logger.info('Begin running tests')
    try:
        logger.info('  Attached ICE supports: %s', i.ice.capabilities)
    except AttributeError:
        logger.info('  Attached ICE does not support querying capabilities')
    logger.info('')

    all_functions = inspect.getmembers(i, inspect.ismethod)
    for f in all_functions:
        if f[0][0:5] == "test_":
            try:
                f[1](ice)
            except ice.VersionError:
                logger.info("%s skipped. Not supported in attached ICE version", f[0])
            except ice.CapabilityError as e:
                logger.info("%s skipped. Required capability %s is not supported",
                        f[0], e.required_capability)
        else:
            logger.warn("Non-test method: " + f[0])

    logger.info('')
    logger.info("All tests completed")
