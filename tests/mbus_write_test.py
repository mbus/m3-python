#!/usr/bin/env python

import datetime
import os
import sys
import struct
import socket
import tempfile
import time
import binascii

import logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger('program')

import threading
import subprocess

import inspect

from m3.ice import ICE
import m3.ice_simulator
import m3.m3_ice

class TestMbusWrite(object):
    class TestFailedException(Exception):
        logger.info('='*42 + '\nTEST FAILED\n' + '='*42)
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
                                        '-t ' + filedir +'/transactions/mbus_write.trx'])

        cls.sim_thread = threading.Thread(
                target=m3.ice_simulator.Simulator(args=sim_args).run,
                name='fake_ice',
                )
        cls.sim_thread.daemon = True
        cls.sim_thread.start()

        # yep, the entire PRCv14/PRCv14_sleep_wakeup.bin file...
        program = ''.join([
        '00200000830000000000000000000000','80000000800000008000000080000000', 
        '80000000800000008000000000000000','80000000800000000000000000000000',
        '15010000210100002d01000045010000','5d010000750100008d010000a5010000',
        'bd010000d5010000f101000001020000','11020000210200000000000000000000',
        'fee700f0d5f8fce70023834200d17047','c0460133f9e7000000290ad0c0239b02',
        '1843054b1860002a02d00122034b1a60','70474004400cf4e7440000a0001300a0',
        '0122044b52421a60ff22034b92001a60','7047c04680e200e000e100e00449054a',
        '0b681a40a0231b0213430b607047c046','280000a0ff0fffff0301024818430160',
        '01207047003000a00022014b1a607047','103000a00122014b1a60704780e200e0',
        '0222014b1a60704780e200e00422034b','1a60034b0c321a607047c04680e200e0',
        '240300000822034b1a60034b09321a60','7047c04680e200e0240300001022034b',
        '1a60034b02321a607047c04680e200e0','240300002022034b1a60034b0d3a1a60',
        '7047c04680e200e0240300004022034b','1a60034b2c3a1a607047c04680e200e0',
        '240300008022034b1a60034b6b3a1a60','7047c04680e200e0240300008022034b',
        '52001a60024bea3a1a60704780e200e0','240300008022044b92001a60034bea3a',
        'ff3a1a607047c04680e200e024030000','8022024bd2001a607047c04680e200e0',
        '8022024b12011a607047c04680e200e0','8022024b52011a607047c04680e200e0',
        '8022024b92011a607047c04680e200e0','70b5fff745ff2a482a4c0368a34234d0',
        '8022294b52011968284d0a431a601a68','27491140802292010a431a601a682549',
        '1140802212020a431a60c0221968d202','0a431a601a68204b1a60046000241f4b',
        '64201c601e4b1c601e4b1c6002232b60','fff7fafe01222868110080b2fff7fcfe',
        '78231c60fff730fffee7fff717ff1649','aa20fff721fffa266420fff7e5fe6425',
        'b60000242100aa20fff716ff2800fff7','dbfe01342800fff7d7feb442f2d1f0e7',
        '14030000eebeadde0c03000030030000','ff9fffffff7ffeff2c0000a010030000',
        '2c030000240300003412cdab08a30600', ])

        fd, cls.tmp_path = tempfile.mkstemp()
        f = open(cls.tmp_path, 'w')
        f.write( binascii.unhexlify(program))
        f.close()
        os.close(fd)

        time.sleep(0.5)

        #cls.ice = ICE()
        #cls.ice.connect(com2)
        #cls.ice.connect(serial_port)

    @classmethod
    def teardown_class(cls):
        m3.ice_simulator.destroy_fake_serial()
        os.remove(cls.tmp_path)

    def test_mbus_write(self):
        logger.info("Testing Mbus Write Feature") 

        serial_port=m3.ice_simulator._FAKE_SERIAL_CONNECTTO_ENDPOINT
        print ('Using ' + str(serial_port))
        self.driver = m3.m3_ice.m3_ice(['--debug',
                                    '-y',
                                    '-s '+ serial_port,
                                    'mbus',
                                    'program',
                                    self.tmp_path])
        
        self.driver.mbus_controller.cmd_program()

                
        # I have no idea how this works.....

# Now I have an idea how this works.....
if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s - %(message)s")

    import nose
    result = nose.run( defaultTest=__name__, )

    if result == True:
        print ('TESTS PASSED')
    else:
        print ('TESTS FAILED')


    logger.info('')
    logger.info("All tests completed")

