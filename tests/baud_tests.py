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

class TestBaud(object):
    class TestFailedException(Exception):
        #logger.info('='*42 + '\nTEST FAILED\n' + '='*42)
        pass

    @classmethod
    def setup_class(cls, ):
       
        #make sure we start with a fresh serial port
        for serial_port in [ m3.ice_simulator._FAKE_SERIAL_SIMULATOR_ENDPOINT, 
                             m3.ice_simulator._FAKE_SERIAL_CONNECTTO_ENDPOINT, ]:
            if os.path.exists(serial_port):
                os.remove(serial_port)

        cls.sim_thread = None
        filedir = os.path.dirname(os.path.realpath(__file__))

        os.environ['ICE_NOSLEEP'] = '1'
        sim_args = m3.ice_simulator.Simulator.get_parser().parse_args([
                                        #'-s ' + serial_port, 
                                        #'-t ' + filedir +'/transactions/timeout.trx',
                                        ])

        cls.sim_thread = threading.Thread(
                target=m3.ice_simulator.Simulator(args=sim_args).run,
                name='fake_ice',
                )
        cls.sim_thread.daemon = True
        cls.sim_thread.start()

        time.sleep(0.5)


    @classmethod
    def teardown_class(cls):
        m3.ice_simulator.destroy_fake_serial()

    def test_baudrate(self):
        logger.info("Testing Baudrate Feature") 
        baudrate = 115200
        
        # baudrate of 2000000 not supported
        # baudrate = 2000000

        serial_port=m3.ice_simulator._FAKE_SERIAL_CONNECTTO_ENDPOINT
        print ('Using ' + str(serial_port))
        self.driver = m3.m3_ice.m3_ice(['--debug',
                                    '--baudrate', str(baudrate),
                                    '-s '+ serial_port,
                                    'snoop'])

        # just something to test baudrate
        logger.info("Test goc on/off")
        
        self.driver.ice.goc_set_onoff(True)
        if self.driver.ice.goc_get_onoff() != True:
            logger.error("Set/get mismatch goc onoff")
            assert(False)

        self.driver.ice.goc_set_onoff(False)
        if self.driver.ice.goc_get_onoff() != False:
            logger.error("Set/get mismatch goc onoff")
            assert(False)

        
        # I have no idea how this works.....
# Now I have an idea how this works.....
if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s - %(message)s")

    import nose
    result = nose.run( defaultTest=__name__, )

    if result == True:
        print 'TESTS PASSED'
    else:
        print 'TESTS FAILED'


    logger.info('')
    logger.info("All tests completed")
