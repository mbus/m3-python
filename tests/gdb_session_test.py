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
logger = logging.getLogger('test_program')

import threading
import subprocess

import inspect

from m3.ice import ICE
import m3.ice_simulator
import m3.m3_ice
from m3.m3_gdb import * 

class TestGdbFull(object):

    # magic nose variable
    _multiprocess_shared_ = False

    class TestFailedException(Exception):
        logger.info('='*42 + '\nTEST FAILED\n' + '='*42)
        pass

    @classmethod
    def setup_class(cls, ):
       
        cls.log = logging.getLogger(cls.__name__)

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
                            '-t ' + filedir +'/transactions/gdb_session.trx'])

        cls.sim_thread = threading.Thread(
                target=m3.ice_simulator.Simulator(args=sim_args).run,
                name='fake_ice',
                )
        cls.sim_thread.daemon = True
        cls.sim_thread.start()

       
    @classmethod
    def teardown_class(cls):
        for serial_port in [ m3.ice_simulator._FAKE_SERIAL_SIMULATOR_ENDPOINT, 
                             m3.ice_simulator._FAKE_SERIAL_CONNECTTO_ENDPOINT, ]:
            if os.path.exists(serial_port):
                os.remove(serial_port)

    def test_gdb_session(this):
        def serv_thread(driver):
            while True:
                try: 
                    driver.mbus_controller.cmd_gdb()
                    return
                except m3.m3_gdb.GdbRemote.PortTakenException as e:
                    this.log.warn("using another port")
                    this.port = int(driver.mbus_controller.m3_ice.args.port) + 1
                    driver.mbus_controller.m3_ice.args.port = str(this.port)
                except:
                    raise

        def cmd_noresp(sock, cmd):
            this.log.debug('TX: ' + cmd)
            s.send(cmd)
            plus = s.recv(1)
            this.log.debug('plus: ' + plus)
            assert(plus == '+')

        def cmd(sock, cmd):
            cmd_noresp(sock, cmd)     
            rx_resp = s.recv(4096)
            this.log.debug('resp: ' + rx_resp)
            s.send('+')
            return rx_resp

        this.log.info("Testing GDB Session")

        this.port = 10001
       
        serial_port=m3.ice_simulator._FAKE_SERIAL_CONNECTTO_ENDPOINT
        this.log.info('Using ' + str(serial_port))

        this.driver = m3.m3_ice.m3_ice([#'--debug',
                                    '-s '+ serial_port,
                                    'mbus',
                                    'gdb',
                                    '--port='+ str(this.port),
                                    ])
        # this time we launch a thread to run the ctrl interface
        servTid= threading.Thread(target = serv_thread, \
                                    args=(this.driver,))
        servTid.daemon = True
        servTid.start()
        
        time.sleep(1.0)  # give time for port to settle

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect( ('localhost',this.port))
        s.send('+')

        this.log.info("starting transaction")

        rx_resp = cmd( s, '$qSupported:multiprocess+;swbreak+;hwbreak+;'\
                                    'qRelocInsn+#c9')
        assert(rx_resp == '$PacketSize=4096#03')

        rx_resp = cmd( s, '$Hg0#df')
        assert(rx_resp == '$#00')

        rx_resp = cmd( s, '$qTStatus#49')
        assert(rx_resp == '$#00')

        rx_resp = cmd( s, '$?#3f')
        assert(rx_resp == '$S05#b8')

        rx_resp = cmd( s, '$qfThreadInfo#bb')
        assert(rx_resp == '$#00')

        rx_resp = cmd( s, '$qL1200000000000000000#50')
        assert(rx_resp == '$#00')

        rx_resp = cmd( s, '$Hc-1#09')
        assert(rx_resp == '$#00')

        rx_resp = cmd( s, '$qC#b4')
        assert(rx_resp == '$#00')

        rx_resp = cmd( s, '$qAttached#8f')
        assert(rx_resp == '$#00')

        rx_resp = cmd( s, '$qOffsets#4b')
        assert(rx_resp == '$#00')

        rx_resp = cmd( s, '$Hg0#df')
        assert(rx_resp == '$#00')

        rx_resp = cmd( s, '$g#67')
        assert(rx_resp == '$6400000020030000280000a06000000020030000e8030000ffffffffffffffff2a000000ffffffffffffffffffffffffffffffff6c1f00000b0400000c0100000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000081#cb')

        rx_resp = cmd( s, '$g#67')
        assert(rx_resp == '$6400000020030000280000a06000000020030000e8030000ffffffffffffffff2a000000ffffffffffffffffffffffffffffffff6c1f00000b0400000c0100000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000081#cb')

        rx_resp = cmd( s, '$qL1200000000000000000#50')
        assert(rx_resp == '$#00')

        rx_resp = cmd( s, '$m10c,4#91')
        assert(rx_resp == '$02d0c046#f3')

        rx_resp = cmd( s, '$m108,2#64')
        assert(rx_resp == '$0023#c5')

        rx_resp = cmd( s, '$m10a,2#8d')
        assert(rx_resp == '$8342#d1')

        rx_resp = cmd( s, '$qSymbol::#5b')
        assert(rx_resp == '$OK#9a')

        rx_resp = cmd( s, '$vCont?#49')
        assert(rx_resp == '$vCont;cs#1b')

        rx_resp = cmd( s, '$Hc0#db')
        assert(rx_resp == '$#00')

        rx_resp = cmd( s, '$s#73')
        assert(rx_resp == '$S05#b8')

        rx_resp = cmd( s, '$g#67')
        assert(rx_resp == '$6400000020030000280000a06000000020030000e8030000ffffffffffffffff2a000000ffffffffffffffffffffffffffffffff6c1f00000b0400000e0100000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000081#cd')

        rx_resp = cmd( s, '$m10e,4#93')
        assert(rx_resp == '$c0baefd0#b5')

        rx_resp = cmd( s, '$m108,2#64')
        assert(rx_resp == '$0023#c5')

        rx_resp = cmd( s, '$m10a,2#8d')
        assert(rx_resp == '$8342#d1')

        rx_resp = cmd( s, '$m10c,2#8f')
        assert(rx_resp == '$02d0#f6')

        rx_resp = cmd( s, '$qL1200000000000000000#50')
        assert(rx_resp == '$#00')

        rx_resp = cmd( s, '$s#73')
        assert(rx_resp == '$S05#b8')
        rx_resp = cmd( s, '$g#67')
        assert(rx_resp == '$6400000020030000280000a06000000020030000e8030000ffffffffffffffff2a000000ffffffffffffffffffffffffffffffff6c1f00000b040000100100000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000081#99')

        rx_resp = cmd( s, '$m110,4#5f')
        assert(rx_resp == '$0133fae7#2a')

        rx_resp = cmd( s, '$m108,2#64')
        assert(rx_resp == '$0023#c5')

        rx_resp = cmd( s, '$m10a,2#8d')
        assert(rx_resp == '$8342#d1')

        rx_resp = cmd( s, '$m10c,2#8f')
        assert(rx_resp == '$02d0#f6')

        rx_resp = cmd( s, '$qL1200000000000000000#50')
        assert(rx_resp == '$#00')

        rx_resp = cmd( s, '$m40a,4#92')
        assert(rx_resp == '$01abcdfe#b6')

        rx_resp = cmd( s, '$m360,2#64')
        assert(rx_resp == '$30b5#fa')

        rx_resp = cmd( s, '$m362,2#66')
        assert(rx_resp == '$4546#d3')

        rx_resp = cmd( s, '$m364,2#68')
        assert(rx_resp == '$20b4#f8')

        rx_resp = cmd( s, '$m366,2#6a')
        assert(rx_resp == '$fff7#69')

        rx_resp = cmd( s, '$m368,2#6c')
        assert(rx_resp == '$fdfe#95')

        rx_resp = cmd( s, '$m40a,2#90')
        assert(rx_resp == '$0134#c8')

        rx_resp = cmd( s, '$Z0,40a,2#d9')
        assert(rx_resp == '$OK#9a')

        rx_resp = cmd( s, '$c#63')
        assert(rx_resp == '$S05#b8')

        rx_resp = cmd( s, '$g#67')
        assert(rx_resp == '$6400000020030000280000a06400000020030000e8030000ffffffffffffffff2a000000ffffffffffffffffffffffffffffffff6c1f00000b0400000a0400000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000061#ce')

        rx_resp = cmd( s, '$m40a,4#92')
        assert(rx_resp == '$01cddffe#bd')

        rx_resp = cmd( s, '$m360,2#64')
        assert(rx_resp == '$30b5#fa')

        rx_resp = cmd( s, '$m362,2#66')
        assert(rx_resp == '$4546#d3')

        rx_resp = cmd( s, '$m364,2#68')
        assert(rx_resp == '$20b4#f8')

        rx_resp = cmd( s, '$m366,2#6a')
        assert(rx_resp == '$fff7#69')

        rx_resp = cmd( s, '$m368,2#6c')
        assert(rx_resp == '$fdfe#95')

        rx_resp = cmd( s, '$qL1200000000000000000#50')
        assert(rx_resp == '$#00')

        rx_resp = cmd( s, '$z0,40a,2#f9')
        assert(rx_resp == '$OK#9a')

        # continue isn't guarenteed an immediate response
        this.log.debug("Sending continue")
        cmd_noresp( s, '$c#63')

        time.sleep(0.1)

        ## CTRL-C occurs a little differently :(
        this.log.debug("Sending CTRL-C")
        s.send( chr(0x03)) # CTRL-C
        # no plus
        rx_resp = s.recv(4096)
        this.log.debug('resp: ' + rx_resp)
        assert(rx_resp == '$S05#b8')
        
        rx_resp = cmd( s, '$g#67')
        assert(rx_resp == '$6400000072030000280000a01a00000072030000e8030000ffffffffffffffff2a000000ffffffffffffffffffffffffffffffff6c1f00000b0400000c0100000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000081#05')

        rx_resp = cmd( s, '$m10c,4#91')
        assert(rx_resp == '$02d0c046#f3')

        rx_resp = cmd( s, '$m108,2#64')
        assert(rx_resp == '$0023#c5')

        rx_resp = cmd( s, '$m10a,2#8d')
        assert(rx_resp == '$8342#d1')

        rx_resp = cmd( s, '$qL1200000000000000000#50')
        assert(rx_resp == '$#00')

        rx_resp = cmd( s, '$m108,2#64')
        assert(rx_resp == '$0023#c5')

        rx_resp = cmd( s, '$m10a,2#8d')
        assert(rx_resp == '$8342#d1')

        rx_resp = cmd( s, '$m10c,2#8f')
        assert(rx_resp == '$02d0#f6')

        rx_resp = cmd( s, '$m10a,2#8d')
        assert(rx_resp == '$8342#d1')

        rx_resp = cmd( s, '$Z0,10a,2#d6')
        assert(rx_resp == '$OK#9a')

        rx_resp = cmd( s, '$c#63')
        assert(rx_resp == '$S05#b8')

        rx_resp = cmd( s, '$g#67')
        assert(rx_resp == '$6400000072030000280000a01b00000072030000e8030000ffffffffffffffff2a000000ffffffffffffffffffffffffffffffff6c1f00000b0400000a0100000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000001#fc')

        rx_resp = cmd( s, '$m10a,4#8f')
        assert(rx_resp == '$01df0023#f0') #$01010101#84')

        rx_resp = cmd( s, '$m108,2#64')
        assert(rx_resp == '$0023#c5')

        rx_resp = cmd( s, '$qL1200000000000000000#50')
        assert(rx_resp == '$#00')

        rx_resp = cmd( s, '$z0,10a,2#f6')
        assert(rx_resp == '$OK#9a')

        rx_resp = cmd( s, '$m100,40#8e')
        assert(rx_resp == '$1d030000d10200000023834202d0c0460133fae7704700000022034b1a60034a106001221a607047001200a0041200a00022014b1a607047001200a0002903d0#1e')

        rx_resp = cmd( s, '$m112,2#5f')
        assert(rx_resp == '$fae7#63')

        rx_resp = cmd( s, '$mffffe7fa,4#c8')
        assert(rx_resp == '$0000a81f#f0')

        rx_resp = cmd( s, '$m112,4#61')
        assert(rx_resp == '$fae70133#2a')

        rx_resp = cmd( s, '$m112,2#5f')
        assert(rx_resp == '$fae7#63')

        rx_resp = cmd( s, '$X112,0:#82')
        assert(rx_resp == '$#00')

        rx_resp = cmd( s, '$M112,2:01df#a4')
        assert(rx_resp == '$OK#9a')

        rx_resp = cmd( s, '$g#67')
        assert(rx_resp == '$6400000072030000280000a01b00000072030000e8030000ffffffffffffffff2a000000ffffffffffffffffffffffffffffffff6c1f00000b0400000a0100000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000001#fc')

        rx_resp = cmd( s, '$m10a,4#8f')
        assert(rx_resp == '$83420023#96') #$83838383#ac')

        rx_resp = cmd( s, '$m108,2#64')
        assert(rx_resp == '$0023#c5')

        rx_resp = cmd( s, '$M112,2:fae7#dc')
        assert(rx_resp == '$OK#9a')

        rx_resp = cmd( s, '$g#67')
        assert(rx_resp == '$6400000072030000280000a01b00000072030000e8030000ffffffffffffffff2a000000ffffffffffffffffffffffffffffffff6c1f00000b0400000a0100000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000001#fc')

        rx_resp = cmd( s, '$m10a,4#8f')
        assert(rx_resp == '$83420023#96') #$83838383#ac')

        rx_resp = cmd( s, '$m108,2#64')
        assert(rx_resp == '$0023#c5')

        rx_resp = cmd( s, '$P9=efbeadde#e6')
        assert(rx_resp == '$OK#9a')

        rx_resp = cmd( s, '$g#67')
        assert(rx_resp == '$6400000072030000280000a01b00000072030000e8030000ffffffffffffffff2a000000efbeaddeffffffffffffffffffffffff6c1f00000b0400000a0100000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000001#ec')

        rx_resp = cmd( s, '$m10a,4#8f')
        assert(rx_resp == '$83420023#96') #$83838383#ac')

        rx_resp = cmd( s, '$m108,2#64')
        assert(rx_resp == '$0023#c5')

        rx_resp = cmd( s, '$P9=cefaedfe#e9')
        assert(rx_resp == '$OK#9a')

        rx_resp = cmd( s, '$g#67')
        assert(rx_resp == '$6400000072030000280000a01b00000072030000e8030000ffffffffffffffff2a000000cefaedfeffffffffffffffffffffffff6c1f00000b0400000a0100000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000001#ef')

        rx_resp = cmd( s, '$m10a,4#8f')
        assert(rx_resp == '$83420023#96') #$83838383#ac')

        rx_resp = cmd( s, '$m108,2#64')
        assert(rx_resp == '$0023#c5')

        rx_resp = cmd( s, '$Hc0#db')
        assert(rx_resp == '$#00')

        rx_resp = cmd( s, '$s#73')
        assert(rx_resp == '$S05#b8')

        rx_resp = cmd( s, '$g#67')
        assert(rx_resp == '$6400000072030000280000a01b00000072030000e8030000ffffffffffffffff2a000000cefaedfeffffffffffffffffffffffff6c1f00000b0400000c0100000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000081#f9')

        rx_resp = cmd( s, '$m10c,4#91')
        assert(rx_resp == '$02d0c046#f3')

        rx_resp = cmd( s, '$m108,2#64')
        assert(rx_resp == '$0023#c5')

        rx_resp = cmd( s, '$m10a,2#8d')
        assert(rx_resp == '$8342#d1')

        rx_resp = cmd( s, '$qL1200000000000000000#50')
        assert(rx_resp == '$#00')

        rx_resp = cmd( s, '$m100,40#8e')
        assert(rx_resp == '$1d030000d10200000023834202d0c0460133fae7704700000022034b1a60034a106001221a607047001200a0041200a00022014b1a607047001200a0002903d0#1e')

        rx_resp = cmd( s, '$m114,2#61')
        assert(rx_resp == '$7047#d2')

        rx_resp = cmd( s, '$Z0,114,2#aa')
        assert(rx_resp == '$OK#9a')

        rx_resp = cmd( s, '$Hc0#db')
        assert(rx_resp == '$#00')

        rx_resp = cmd( s, '$c#63')
        assert(rx_resp == '$S05#b8')

        rx_resp = cmd( s, '$g#67')
        assert(rx_resp == '$6400000072030000280000a06400000072030000e8030000ffffffffffffffff2a000000cefaedfeffffffffffffffffffffffff6c1f00000b040000140100000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000061#a0')

        rx_resp = cmd( s, '$m114,4#63')
        assert(rx_resp == '$01df0000#eb')

        rx_resp = cmd( s, '$m108,2#64')
        assert(rx_resp == '$0023#c5')

        rx_resp = cmd( s, '$m10a,2#8d')
        assert(rx_resp == '$8342#d1')

        rx_resp = cmd( s, '$m10c,2#8f')
        assert(rx_resp == '$02d0#f6')

        rx_resp = cmd( s, '$qL1200000000000000000#50')
        assert(rx_resp == '$#00')

        rx_resp = cmd( s, '$z0,114,2#ca')
        assert(rx_resp == '$OK#9a')

        rx_resp = cmd( s, '$Hc0#db')
        assert(rx_resp == '$#00')

        rx_resp = cmd( s, '$s#73')
        assert(rx_resp == '$S05#b8')

        rx_resp = cmd( s, '$g#67')
        assert(rx_resp == '$6400000072030000280000a06400000072030000e8030000ffffffffffffffff2a000000cefaedfeffffffffffffffffffffffff6c1f00000b0400000a0400000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000061#cf')

        rx_resp = cmd( s, '$m40a,4#92')
        assert(rx_resp == '$01347ffe#30') #$01010101#84')

        rx_resp = cmd( s, '$m360,2#64')
        assert(rx_resp == '$30b5#fa')

        rx_resp = cmd( s, '$m362,2#66')
        assert(rx_resp == '$4546#d3')

        rx_resp = cmd( s, '$m364,2#68')
        assert(rx_resp == '$20b4#f8')

        rx_resp = cmd( s, '$m366,2#6a')
        assert(rx_resp == '$fff7#69')

        rx_resp = cmd( s, '$m368,2#6c')
        assert(rx_resp == '$fdfe#95')


        rx_resp = cmd( s, '$m1f78,4#d3')
        assert(rx_resp == '$2c229c8d#31')

        rx_resp = cmd( s, '$m8d9c222c,4#fe')
        assert(rx_resp == '$a81f0000#f0')

        rx_resp = cmd( s, '$m1f78,4#d3')
        assert(rx_resp == '$2c229c8d#31')

        rx_resp = cmd( s, '$m1f78,4#d3')
        assert(rx_resp == '$2c229c8d#31')

        rx_resp = cmd( s, '$mffffffff,4#fd')
        assert(rx_resp == '$00a81f00#f0') #$00000000#80')

        rx_resp = cmd( s, '$qL1200000000000000000#50')
        assert(rx_resp == '$#00')

        rx_resp = cmd( s, '$D#44')
        assert(rx_resp == '$OK#9a')

        #need to add ice_terminate? 
        s.close()

        servTid.join()
        

# Now I have an idea how this works.....
if __name__ == '__main__':

    #logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s - %(message)s")

    import nose
    result = nose.run( defaultTest=__name__, )

    if result == True:
        print 'TESTS PASSED'
    else:
        print 'TESTS FAILED'

