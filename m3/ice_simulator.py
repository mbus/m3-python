#!/usr/bin/env python

# Coerce Py2k to act more like Py3k
from __future__ import (absolute_import, division, print_function, unicode_literals)
from builtins import (
        ascii, bytes, chr, dict, filter, hex, input, int, isinstance, list, map,
        next, object, oct, open, pow, range, round, str, super, zip,
        )

CAPABILITES = "?_dIifnOoBbMmeGgPp"
MAX_GPIO = 24
DEFAULT_BAUD_DIVIDER = 0x00AE
DEFAULT_I2C_MASK = '1001100x'
DEFAULT_I2C_SPEED_IN_KHZ = 100
DEFAULT_FLOW_CLOCK_IN_HZ = .625
DEFAULT_POWER_0P6 = 0.675
DEFAULT_POWER_1P2 = 1.2
DEFAULT_POWER_VBATT = 3.8
DEFAULT_VSET_0P6 = 19
DEFAULT_VSET_1P2 = 25
DEFAULT_VSET_VBATT = 25
DEFAULT_MBUS_FULL_PREFIX_ONES = 0xfffff0
DEFAULT_MBUS_FULL_PREFIX_ZEROS = 0xfffff0
DEFAULT_MBUS_SHORT_PREFIX = 0x0f
DEFAULT_MBUS_BROADCAST_MASK_ONES = 0x0f
DEFAULT_MBUS_BROADCAST_MASK_ZEROS = 0x0f
DEFAULT_MBUS_SNOOP_BROADCAST_MASK_ONES = 0x0f
DEFAULT_MBUS_SNOOP_BROADCAST_MASK_ZEROS = 0x0f


import argparse
import atexit
import binascii
import datetime
import os
import platform
import random
import serial
import struct
import subprocess
import sys
import tempfile
import time
import threading
import traceback


from . import m3_logging
logger = m3_logging.get_logger(__name__)

from .ice import ICE

class UnknownCommandException(Exception):
    pass


class Simulator(object):
    def __init__(self, args=None):
        if args is None:
            self.parse_cli()
        else:
            self.args = args

        self.baud_divider = DEFAULT_BAUD_DIVIDER

        self.i2c_mask_ones = 0
        self.i2c_mask_zeros = 0
        for bit in self.args.i2c_mask:
            self.i2c_mask_ones <<= 1
            self.i2c_mask_zeros <<= 1
            self.i2c_mask_ones |= (bit == '1')
            self.i2c_mask_zeros |= (bit == '0')
        logger.debug("mask %s ones %02x zeros %02x", self.args.i2c_mask,
                self.i2c_mask_ones, self.i2c_mask_zeros)

        self.i2c_speed_in_khz = DEFAULT_I2C_SPEED_IN_KHZ

        self.flow_clock_in_hz = DEFAULT_FLOW_CLOCK_IN_HZ
        self.flow_onoff = False

        self.vset_0p6 = DEFAULT_VSET_0P6
        self.vset_1p2 = DEFAULT_VSET_1P2
        self.vset_vbatt = DEFAULT_VSET_VBATT
        self.power_0p6_on = False
        self.power_1p2_on = False
        self.power_vbatt_on = False
        self.power_goc_on = False

        self.mbus_full_prefix_ones = DEFAULT_MBUS_FULL_PREFIX_ONES
        self.mbus_full_prefix_zeros = DEFAULT_MBUS_FULL_PREFIX_ZEROS
        self.mbus_short_prefix = DEFAULT_MBUS_SHORT_PREFIX
        self.mbus_snoop_enabled = False
        self.mbus_broadcast_mask_ones = DEFAULT_MBUS_BROADCAST_MASK_ONES
        self.mbus_broadcast_mask_zeros = DEFAULT_MBUS_BROADCAST_MASK_ZEROS
        self.mbus_snoop_broadcast_mask_ones = DEFAULT_MBUS_SNOOP_BROADCAST_MASK_ONES
        self.mbus_snoop_broadcast_mask_zeros = DEFAULT_MBUS_SNOOP_BROADCAST_MASK_ZEROS
        self.mbus_ismaster = False
        self.mbus_should_interrupt = 0
        self.mbus_should_prio = 0
        self.mbus_force_reset = 0


        self.s_lock = threading.Lock()
        self.s_en_event = threading.Event()

        logger.debug('Serial:' + str(self.args.serial))
        if self.args.serial == _FAKE_SERIAL_SIMULATOR_ENDPOINT:
            if not os.path.exists(_FAKE_SERIAL_SIMULATOR_ENDPOINT):
                create_fake_serial()

        try:
            if self.args.serial == _FAKE_SERIAL_SIMULATOR_ENDPOINT:
                # Workaround for https://github.com/pyserial/pyserial/issues/59
                #            and https://github.com/pyserial/pyserial/issues/113
                serial.Serial._update_dtr_state = lambda self : None
                serial.Serial._update_rts_state = lambda self : None
            self.s = serial.Serial(self.args.serial, 115200)
        except:
            logger.error("Opening serial failed")
            logger.error("")
            logger.error("If you need to create a software serial tunnel, use socat:")
            logger.error("  socat -x pty,link=/tmp/com1,raw,echo=0 pty,link=/tmp/com2,raw,echo=0")
            logger.error("")
            raise
        if not self.s.isOpen():
            logger.error('Could not open serial port at: ' + self.args.serial)
            raise IOError("Failed to open serial port")


        self.event = 0
        self.gpios = [Gpio() for x in range(MAX_GPIO)]

        if self.args.generate_messages:
            self.gen_thread = threading.Thread(target=self.spurious_message_thread)
            self.gen_thread.daemon = True
            self.gen_thread.start()

        if self.args.replay is not None:
            self.replay_thread = threading.Thread(target=self.replay_message_thread)
            self.replay_thread.daemon = True
            self.replay_thread.start()


    def run(self, background=False):
        if background:
            self.main_thread = threading.Thread(target=self.main_loop)
            self.main_thread.daemon = True
            self.main_thread.start()
        else:
            logger.info("-" * 80)
            logger.info("-- M3 ICE Interface Board Simulator")
            logger.info("")

            if self.args.transaction:
                #skip multithreading here
                self.transaction_mode()
            else:
                self.main_loop()


    def spurious_message_thread(self):
        def send_snoop(addr, data, control):
            logger.info("Send generated message ADDR: 0x{}   DATA: 0x{}   CTL: 0x{}".\
                    format(addr, data, control))
            with self.s_lock:
                self.s.write(bytes('B', 'utf-8'))
                self.s.write(bytes((self.event,)))
                self.event += 1
                self.event %= 256

                addr = binascii.unhexlify(addr)
                data = binascii.unhexlify(data)
                control = binascii.unhexlify(control)
                length = len(addr) + len(data) + len(control)

                self.s.write(bytes((length,)))
                self.s.write(addr)
                self.s.write(data)
                self.s.write(control)

        while True:
            # control:
            #   b0 b1 -> val -> meaning
            #  - 0, 0     0     General Error
            #  - 0, 1     2     TX or RX Error
            #  - 1, 0     1     ACK
            #  - 1, 1     3     NAK
            for args in (
                    ('00000074', 'deadbeef', '01'),
                    ('00000040', 'ab', '01'),
                    ('f0012345', '0123456789abcdef', '03'),
                    ('00000022', 'a5'*160, '01'),
                    ('00000033', 'c9'*160, '01'),
                    ('00000044', 'ef'*160, '01'),
                    ):
                self.sleep(random.randint(1,12))
                if not self.mbus_snoop_enabled:
                    continue
                send_snoop(*args)

    def replay_message_thread(self):
        def send_snoop(addr, data, control):
            with self.s_lock:
                self.s.write(bytes('B', 'utf-8'))
                self.s.write(bytes((self.event,)))
                self.event += 1
                self.event %= 256

                addr = binascii.unhexlify(addr)
                data = binascii.unhexlify(data)
                control = binascii.unhexlify(control)
                length = len(addr) + len(data) + len(control)

                self.s.write(bytes((length,)))
                self.s.write(addr)
                self.s.write(data)
                self.s.write(control)

        logger.info("Replay thread waiting for snoop to be enabled")
        self.s_en_event.wait()
        logger.info("Replay beginning")
        last_ts = None
        for line in open(self.args.replay):
            assert self.mbus_snoop_enabled

            ts,addr,data = line.strip().split(',')
            if len(addr) == 2:
                addr = '000000' + addr
            else:
                assert len(addr) == 8
            ts = float(ts)

            #if last_ts is not None:
            #    logger.info("sleep for {}".format(ts - last_ts))
            #    self.sleep(ts - last_ts)
            last_ts = ts

            print(data)
            print(len(data)/2.0)
            send_snoop(addr, data, '02')

        logger.info("Replay finished.")

    #
    #
    #
    def transaction_mode(self):
        ''' 
        Replays a series of ICE transactions with timing information
        '''
        def read_raw_message():
            msg_type, event_id, length = self.s.read(3)
            length_int = ord(length)
            logger.debug("Got a message of type: " + msg_type + 
                    ' length: ' + str(length_int))
            msg = self.s.read(length_int)

            return msg_type + event_id + length + msg

        logger.info("Transaction beginning")
        last_ts = None

        for line in open( self.args.transaction.strip() ):
            
            line = line.strip('\n')
            line = line.strip('\r')

            logger.info('Working on: ' + line)

            if len(line) == 0: continue
            elif line[0] in ['#', ' ', '/', ]: continue

            elif line.startswith('WAIT'):
               
                #find the number after T
                hex_tex = line.split('WAIT')[1].strip()
                hex_tex = hex_tex.replace('0x', '').lower()
                data = binascii.unhexlify(hex_tex)
               
                rxMsg = b''

                rxMsg = read_raw_message()
                logger.debug('Read: ' + binascii.hexlify(rxMsg))
                logger.info(' vs  : ' + binascii.hexlify(data))
                if (rxMsg != data): 
                    rx = binascii.hexlify(rxMsg)
                    buf = binascii.hexlify(data) 
                    raise Exception('Read vs. Expect: ' + \
                            str(rx) + ' vs. ' + str(buf)  + \
                            ' ascii: ' + str(rx==buf))
                logger.debug('Found match!') 

            elif line.startswith('SEND'):
                # find the number after N (D could also be hex...)
                hex_tex = line.split('SEND')[1].strip()
                hex_tex = hex_tex.replace('0x', '').lower()
                data = binascii.unhexlify(hex_tex)
                print ('SENDING: ' + binascii.hexlify(data))
                self.s.write(data)
                self.s.flush()

            elif line.startswith('SLEEP'): 
                time_str = line.replace(' ','').split('P')[1]
                time_sec = float(time_str)
                time.sleep(time_sec)
                self.s.flush()



            else: raise Exception('Unknown Command: "' + repr(line) + '"')

        time.sleep(0.5)
        self.s.close()

        logger.info("Transaction finished.")


    def main_loop(self):
        self.i2c_msg = bytes()
        self.i2c_match = True
        self.flow_msg = bytes()
        self.ein_msg = bytes()
        self.mbus_msg = bytes()
        while True:
            def min_proto(proto):
                if minor < proto:
                    logger.error("Request for protocol 0.2 command, but the")
                    logger.error("negotiated protocol was 0.1")
                    raise UnknownCommandException

            try:
                msg_type, event_id, length = self.s.read(3)
                logger.debug("Got a message of type: " + msg_type)
                event_id = ord(event_id)
                length = ord(length)
                msg = self.s.read(length)
    
                #slight hack to simplify respond()
                self.event = event_id

                if msg_type == 'V':
                    if self.args.ice_version == 1:
                        self.respond(binascii.unhexlify('0001'))
                    elif self.args.ice_version == 2:
                        self.respond(binascii.unhexlify('0001'))
                    elif self.args.ice_version == 3:
                        self.respond(binascii.unhexlify('000300020001'))
                    elif self.args.ice_version == 4:
                        self.respond(binascii.unhexlify('0004000300020001'))
                    else:
                        raise ValueError("Unknown ice version: %d" % (self.args.ice_version))
                elif msg_type == 'v':
                    CLOCK_FREQ = 4e6
                    if msg == binascii.unhexlify('0004'):
                        minor = 4
                    elif msg == binascii.unhexlify('0003'):
                        minor = 3
                    elif msg == binascii.unhexlify('0002'):
                        minor = 2
                    elif msg == binascii.unhexlify('0001'):
                        CLOCK_FREQ = 2e6
                        minor = 1
                    else:
                        logger.error("Request for unknown version: " + msg)
                        raise Exception
                    logger.info("Negotiated to protocol version 0."+ str(minor))
                    self.ack()

                elif msg_type == '?':
                    min_proto(2)
                    if msg[0] == '?':
                        logger.info("Responded to query capabilites with " + CAPABILITES)
                        self.respond(CAPABILITES)
                    elif msg[0] == 'b':
                        logger.info("Responded to query for ICE baudrate (divider: 0x%04X)" % (self.baud_divider))
                        self.respond(struct.pack('>H', self.baud_divider))
                    else:
                        logger.error("Bad '?' subtype: " + msg[0])
                        raise UnknownCommandException
                elif msg_type == '_':
                    min_proto(2)
                    if msg[0] == 'b':
                        high = ord(msg[1])
                        low = ord(msg[2])
                        new_div = low | (high << 8)
                        if new_div not in (0x00AE, 0x000A, 0x0007):
                            logger.error("Bad baudrate divider: 0x%04X" % (new_div))
                            raise Exception
                        self.ack()
                        try:
                            if new_div == 0x00AE:
                                self.s.baudrate = 115200
                            elif new_div == 0x000A:
                                self.s.baudrate = 2000000
                            elif new_div == 0x0007:
                                self.s.baudrate = 3000000
                            else:
                                logger.error("Unknown baudrate divider")
                                raise Exception
                        except IOError as e:
                            if e.errno == 25:
                                logger.warn("Failed to set baud rate (if socat, ignore)")
                            else:
                                raise
                        self.baud_divider = new_div
                        logger.info("New baud divider set: " + str(self.baud_divider))
                    else:
                        logger.error("bad '_' subtype: " + msg[0])
                        raise UnknownCommandException
                elif msg_type == 'b':
                    min_proto(2)
                    self.mbus_msg += msg
                    if len(msg) != 255:
                        logger.info("Got a MBus message:")
                        logger.info("   message: " + binascii.hexlify(self.mbus_msg))
                        self.mbus_msg = bytes()
                        if self.mbus_should_interrupt:
                            logger.info("Message would have interrupted")
                            if self.mbus_should_interrupt == 1:
                                self.mbus_should_interrupt = 0
                        if self.mbus_should_prio:
                            logger.info("Message would have been sent high priority")
                            if self.mbus_should_prio == 1:
                                self.mbus_should_prio = 0
                    else:
                        logger.debug("Got MBus fragment")
                    self.ack()
                elif msg_type == 'd':
                    self.i2c_msg += msg
                    if not self.i2c_match:
                        if not self.match_mask(ord(msg[0]), self.i2c_mask_ones, self.i2c_mask_zeros):
                            logger.info("I2C address %02x did not match mask %02x %02x",
                                    ord(msg[0]), self.i2c_mask_ones, self.i2c_mask_zeros)
                            self.respond(struct.pack('B', 0), ack=False)
                            continue
                        self.i2c_match = True
                    if len(msg) != 255:
                        logger.info("Got i2c message:")
                        logger.info("  addr: " + binascii.hexlify(self.i2c_msg[0:1]))
                        logger.info("  data: " + binascii.hexlify(self.i2c_msg[1:]))
                        self.i2c_msg = bytes()
                        self.i2c_match = False
                    else:
                        logger.debug("Got i2c fragment")
                    self.ack()
                elif msg_type == 'e':
                    min_proto(2)
                    self.ein_msg += msg
                    if len(msg) != 255:
                        logger.info("Got a EIN message:")
                        logger.info("  message: " + binascii.hexlify(self.ein_msg))
                        self.ein_msg = bytes()
                    else:
                        logger.debug("Got EIN fragment")
                    self.ack()
                elif msg_type in ('f', 'n'):
                    self.flow_msg += msg
                    if len(msg) != 255:
                        logger.info("Got f/n-type message in %s mode:", ('EIN','GOC')[ein_goc_toggle])
                        logger.info("  message: " + binascii.hexlify(self.flow_msg))
                        self.flow_msg = bytes()
                    else:
                        logger.debug("Got f/n-type fragment in %s mode", ('EIN','GOC')[ein_goc_toggle])
                    if ein_goc_toggle:
                        t = (len(msg)*8) / self.flow_clock_in_hz
                        logger.info("Sleeping for {} seconds to mimic GOC".format(t))
                        try:
                            self.sleep(t)
                        except KeyboardInterrupt:
                            pass
                    self.ack()
                elif msg_type == 'G':
                    # GPIO changed completely between v0.1 and v0.2
                    if minor == 1:
                        if msg[0] == 'l':
                            logger.info("Responded to request for GPIO %d Dir (%s)", ord(msg[1]), self.gpios[ord(msg[1])])
                            self.respond(struct.pack("B", self.gpios[ord(msg[1])].level))
                        elif msg[0] == 'd':
                            logger.info("Responded to request for GPIO %d Level (%s)", ord(msg[1]), self.gpios[ord(msg[1])])
                            self.respond(struct.pack("B", self.gpios[ord(msg[1])].direction))
                        else:
                            logger.error("bad 'G' subtype: " + msg[0])
                            raise Exception
                    else:
                        if msg[0] == 'l':
                            mask = 0
                            for i in range(len(self.gpios)):
                                mask |= (self.gpios[i].level << i)
                            logger.info("Responded to request for GPIO level mask (%06x)", mask)
                            self.respond(struct.pack('>I', mask)[1:])
                        elif msg[0] == 'd':
                            mask = 0
                            for i in range(len(self.gpios)):
                                mask |= (self.gpios[i].direction << i)
                            logger.info("Responded to request for GPIO direction mask (%06x)", mask)
                            self.respond(struct.pack('>I', mask)[1:])
                        elif msg[0] == 'i':
                            mask = 0
                            for i in range(len(self.gpios)):
                                mask |= (self.gpios[i].interrupt << i)
                            logger.info("Responded to request for GPIO interrupt mask (%06x)", mask)
                            self.respond(struct.pack('>I', mask)[1:])
                        else:
                            logger.error("bad 'G' subtype: " + msg[0])
                            raise Exception
                elif msg_type == 'g':
                    # GPIO changed completely between v0.1 and v0.2
                    if minor == 1:
                        if msg[0] == 'l':
                            self.gpios[ord(msg[1])].level = (ord(msg[2]) == True)
                            logger.info("Set GPIO %d Level: %s", ord(msg[1]), self.gpios[ord(msg[1])])
                            self.ack()
                        elif msg[0] == 'd':
                            self.gpios[ord(msg[1])].direction = ord(msg[2])
                            logger.info("Set GPIO %d Dir: %s", ord(msg[1]), self.gpios[ord(msg[1])])
                            self.ack()
                        else:
                            logger.error("bad 'g' subtype: " + msg[0])
                            raise Exception
                    else:
                        if msg[0] == 'l':
                            high,mid,low = map(ord, msg[1:])
                            mask = low | mid << 8 | high << 16
                            for i in range(24):
                                self.gpios[i].level = (mask >> i) & 0x1
                            logger.info("Set GPIO level mask to: %06x", mask)
                            self.ack()
                        elif msg[0] == 'd':
                            high,mid,low = map(ord, msg[1:])
                            mask = low | mid << 8 | high << 16
                            for i in range(24):
                                self.gpios[i].direction = (mask >> i) & 0x1
                            logger.info("Set GPIO direction mask to: %06x", mask)
                            self.ack()
                        elif msg[0] == 'i':
                            high,mid,low = map(ord, msg[1:])
                            mask = low | mid << 8 | high << 16
                            for i in range(24):
                                self.gpios[i].interrupt = (mask >> i) & 0x1
                            logger.info("Set GPIO interrupt mask to: %06x", mask)
                            self.ack()
                        else:
                            logger.error("bad 'g' subtype: " + msg[0])
                            raise Exception
                elif msg_type == 'I':
                    if msg[0] == 'c':
                        logger.info("Responded to query for I2C bus speed (%d kHz)", self.i2c_speed_in_khz)
                        self.respond(struct.pack("B", self.i2c_speed_in_khz // 2))
                    elif msg[0] == 'a':
                        logger.info("Responded to query for ICE I2C mask (%02x ones %02x zeros)",
                                self.i2c_mask_ones, self.i2c_mask_zeros)
                        self.respond((self.i2c_mask_ones, self.i2c_mask_zeros))
                    else:
                        logger.error("bad 'I' subtype: " + msg[0])
                        raise Exception
                elif msg_type == 'i':
                    if msg[0] == 'c':
                        self.i2c_speed_in_khz = ord(msg[1]) * 2
                        logger.info("I2C Bus Speed set to %d kHz", self.i2c_speed_in_khz)
                        self.ack()
                    elif msg[0] == 'a':
                        self.i2c_mask_ones = ord(msg[1])
                        self.i2c_mask_zeros = ord(msg[2])
                        logger.info("ICE I2C mask set to 0x%02x ones, 0x%02x zeros",
                                self.i2c_mask_ones, self.i2c_mask_zeros)
                        self.ack()
                    else:
                        logger.error("bad 'i' subtype: " + msg[0])
                        raise Exception
                elif msg_type == 'M':
                    min_proto(2)
                    if msg[0] == 'l':
                        logger.info("Responded to query for MBus full prefix mask (%06x ones %06x zeros)",
                                self.mbus_full_prefix_ones, self.mbus_full_prefix_zeros)
                        r = struct.pack('>I', self.mbus_full_prefix_ones)[1:]
                        r += struct.pack('>I', self.mbus_full_prefix_zeros)[1:]
                        self.respond(r)
                    elif msg[0] == 's':
                        logger.info("Responded to query for MBus short prefix (%02x)",
                                self.mbus_short_prefix)
                        self.respond(struct.pack("B", self.mbus_short_prefix))
                    elif msg[0] == 'S':
                        logger.info("Responded to query for MBus snoop enabled (%d)",
                                self.mbus_snoop_enabled)
                        self.respond(struct.pack("B", self.mbus_snoop_enabled))
                    elif msg[0] == 'b':
                        logger.info("Responded to query for MBus broadcast mask (%02x ones %02x zeros)",
                                self.mbus_broadcast_mask_ones, self.mbus_broadcast_mask_zeros)
                        self.respond(struct.pack("BB",
                            self.mbus_broadcast_mask_ones,
                            self.mbus_broadcast_mask_zeros))
                    elif msg[0] == 'B':
                        logger.info("Responded to query for MBus snoop broadcast mask (%02x ones %02x zeros)",
                                self.mbus_snoop_broadcast_mask_ones, self.mbus_snoop_broadcast_mask_zeros)
                        self.respond(struct.pack("BB",
                            self.mbus_snoop_broadcast_mask_ones,
                            self.mbus_snoop_broadcast_mask_zeros))
                    elif msg[0] == 'm':
                        logger.info("Responded to query for MBus master state (%s)",
                                ("off", "on")[self.mbus_ismaster])
                        self.respond(struct.pack("B", self.mbus_ismaster))
                    elif msg[0] == 'c':
                        raise NotImplementedError("MBus clock not defined")
                    elif msg[0] == 'i':
                        logger.info("Responded to query for MBus should interrupt (%d)",
                                self.mbus_should_interrupt)
                        self.respond(struct.pack("B", self.mbus_should_interrupt))
                    elif msg[0] == 'p':
                        logger.info("Responded to query for MBus should use priority arb (%d)",
                                self.mbus_should_prio)
                        self.respond(struct.pack("B", self.mbus_should_prio))
                    elif msg[0] == 'r':
                        logger.info("Responded to query for MBus internal reset (%d)",
                                self.mbus_force_reset)
                        self.respond(struct.pack("B", self.mbus_force_reset))
                    else:
                        logger.error("bad 'M' subtype: " + msg[0])
                elif msg_type == 'm':
                    min_proto(2)
                    if msg[0] == 'l':
                        self.mbus_full_prefix_ones = ord(msg[3])
                        self.mbus_full_prefix_ones |= ord(msg[2]) << 8
                        self.mbus_full_prefix_ones |= ord(msg[1]) << 16
                        self.mbus_full_prefix_zeros = ord(msg[6])
                        self.mbus_full_prefix_zeros |= ord(msg[5]) << 8
                        self.mbus_full_prefix_zeros |= ord(msg[4]) << 16
                        logger.info("MBus full prefix mask set to ones %06x zeros %06x",
                                self.mbus_full_prefix_ones, self.mbus_full_prefix_zeros)
                        self.ack()
                    elif msg[0] == 's':
                        self.mbus_short_prefix = ord(msg[1])
                        logger.info("MBus short prefix set to %02x", self.mbus_short_prefix)
                        self.ack()
                    elif msg[0] == 'S':
                        self.mbus_snoop_enabled = ord(msg[1])
                        if self.mbus_snoop_enabled:
                            self.s_en_event.set()
                        logger.info("MBus snoop enabled set to %d", self.mbus_snoop_enabled)
                        self.ack()
                    elif msg[0] == 'b':
                        self.mbus_broadcast_mask_ones = ord(msg[1])
                        self.mbus_broadcast_mask_zeros = ord(msg[2])
                        logger.info("MBus broadcast mask set to ones %02x zeros %02x",
                                self.mbus_broadcast_mask_ones, self.mbus_broadcast_mask_zeros)
                        self.ack()
                    elif msg[0] == 'B':
                        self.mbus_snoop_broadcast_mask_ones = ord(msg[1])
                        self.mbus_snoop_broadcast_mask_zeros = ord(msg[2])
                        logger.info("MBus snoop broadcast mask set to ones %02x zeros %02x",
                                self.mbus_snoop_broadcast_mask_ones, self.mbus_snoop_broadcast_mask_zeros)
                        self.ack()
                    elif msg[0] == 'm':
                        self.mbus_ismaster = bool(ord(msg[1]))
                        logger.info("MBus master mode set " + ("off", "on")[self.mbus_ismaster])
                        self.ack()
                    elif msg[0] == 'c':
                        raise NotImplementedError("MBus clock not defined")
                    elif msg[0] == 'i':
                        self.mbus_should_interrupt = ord(msg[1])
                        logger.info("MBus should interrupt set to %d", self.mbus_should_interrupt)
                        self.ack()
                    elif msg[0] == 'p':
                        self.mbus_should_prio = ord(msg[1])
                        logger.info("MBus should use priority arbitration set to %d",
                                self.mbus_should_prio)
                        self.ack()
                    elif msg[0] == 'r':
                        self.mbus_force_reset = ord(msg[1])
                        logger.info("MBus internal reset set to %d", self.mbus_force_reset)
                        self.ack()
                    else:
                        logger.error("bad 'm' subtype: " + msg[0])
                elif msg_type == 'O':
                    if msg[0] == 'c':
                        logger.info("Responded to query for FLOW clock (%.2f Hz)", self.flow_clock_in_hz)
                        div = int(CLOCK_FREQ / self.flow_clock_in_hz)
                        resp = ''
                        if minor >= 3:
                            resp = struct.pack(">I", div)
                        else:
                            resp = struct.pack(">I", div)[1:]
                        self.respond(resp)
                    elif msg[0] == 'o':
                        if minor > 1:
                            logger.info("Responded to query for FLOW power (%s)", ('off','on')[self.flow_onoff])
                            self.respond(struct.pack("B", self.flow_onoff))
                        else:
                            logger.error("Request for protocol 0.2 command (Oo), but the")
                            logger.error("negotiated protocol was 0.1")
                    else:
                        logger.error("bad 'O' subtype: " + msg[0])
                elif msg_type == 'o':
                    if msg[0] == 'c':
                        if minor >= 3:
                            div = (ord(msg[1]) << 24) | (ord(msg[2]) << 16) | (ord(msg[3]) << 8) | ord(msg[4])
                        else:
                            div = (ord(msg[1]) << 16) | (ord(msg[2]) << 8) | ord(msg[3])
                        self.flow_clock_in_hz = CLOCK_FREQ / div
                        logger.info("Set FLOW clock to %.2f Hz", self.flow_clock_in_hz)
                        self.ack()
                    elif msg[0] == 'o':
                        min_proto(2)
                        if minor > 1:
                            self.flow_onoff = bool(ord(msg[1]))
                            logger.info("Set FLOW power to %s", ('off','on')[self.flow_onoff])
                            self.ack()
                    elif msg[0] == 'p':
                        min_proto(2)
                        ein_goc_toggle = bool(ord(msg[1]))
                        logger.info("Set GOC/EIN toggle to %s mode", ('EIN','GOC')[ein_goc_toggle])
                        self.ack()
                    else:
                        assert False
                        logger.error("bad 'o' subtype: " + msg[0])
                        assert False
                elif msg_type == 'P':
                    pwr_idx = ord(msg[1])
                    if pwr_idx not in (0,1,2):
                        logger.error("Illegal power index: %d", pwr_idx)
                        raise Exception
                    if msg[0] == 'v':
                        if pwr_idx is 0:
                            logger.info("Query 0.6V rail (vset=%d, vout=%.2f)", self.vset_0p6,
                                    (0.537 + 0.0185 * self.vset_0p6) * DEFAULT_POWER_0P6)
                            self.respond(struct.pack("BB", pwr_idx, self.vset_0p6))
                        elif pwr_idx is 1:
                            logger.info("Query 1.2V rail (vset=%d, vout=%.2f)", self.vset_1p2,
                                    (0.537 + 0.0185 * self.vset_1p2) * DEFAULT_POWER_1P2)
                            self.respond(struct.pack("BB", pwr_idx, self.vset_1p2))
                        elif pwr_idx is 2:
                            logger.info("Query VBatt rail (vset=%d, vout=%.2f)", self.vset_vbatt,
                                    (0.537 + 0.0185 * self.vset_vbatt) * DEFAULT_POWER_VBATT)
                            self.respond(struct.pack("BB", pwr_idx, self.vset_vbatt))
                    elif msg[0] == 'o':
                        if pwr_idx is 0:
                            logger.info("Query 0.6V rail (%s)", ('off','on')[self.power_0p6_on])
                            self.respond(struct.pack("B", self.power_0p6_on))
                        elif pwr_idx is 1:
                            logger.info("Query 1.2V rail (%s)", ('off','on')[self.power_1p2_on])
                            self.respond(struct.pack("B", self.power_1p2_on))
                        elif pwr_idx is 2:
                            logger.info("Query vbatt rail (%s)", ('off','on')[self.power_vbatt_on])
                            self.respond(struct.pack("B", self.power_vbatt_on))
                        elif pwr_idx is 3:
                            logger.info("Query goc rail (%s)", ('off','on')[self.power_goc_on])
                            self.respond(struct.pack("B", self.power_goc_on))
                    else:
                        logger.error("bad 'p' subtype: " + msg[0])
                        raise Exception
                elif msg_type == 'p':
                    pwr_idx = ord(msg[1])
                    if msg[0] == 'v':
                        if pwr_idx is ICE.POWER_0P6:
                            self.vset_0p6 = ord(msg[2])
                            logger.info("Set 0.6V rail to vset=%d, vout=%.2f", self.vset_0p6,
                                    (0.537 + 0.0185 * self.vset_0p6) * DEFAULT_POWER_0P6)
                        elif pwr_idx is ICE.POWER_1P2:
                            self.vset_1p2 = ord(msg[2])
                            logger.info("Set 1.2V rail to vset=%d, vout=%.2f", self.vset_1p2,
                                    (0.537 + 0.0185 * self.vset_1p2) * DEFAULT_POWER_1P2)
                        elif pwr_idx is ICE.POWER_VBATT:
                            self.vset_vbatt = ord(msg[2])
                            logger.info("Set VBatt rail to vset=%d, vout=%.2f", self.vset_vbatt,
                                    (0.537 + 0.0185 * self.vset_vbatt) * DEFAULT_POWER_VBATT)
                        else:
                            logger.error("Illegal power index: %d", pwr_idx)
                            raise Exception
                        self.ack()
                    elif msg[0] == 'o':
                        if pwr_idx is ICE.POWER_0P6:
                            self.power_0p6_on = bool(ord(msg[2]))
                            logger.info("Set 0.6V rail %s", ('off','on')[self.power_0p6_on])
                        elif pwr_idx is ICE.POWER_1P2:
                            self.power_1p2_on = bool(ord(msg[2]))
                            logger.info("Set 1.2V rail %s", ('off','on')[self.power_1p2_on])
                        elif pwr_idx is ICE.POWER_VBATT:
                            self.power_vbatt_on = bool(ord(msg[2]))
                            logger.info("Set VBatt rail %s", ('off','on')[self.power_vbatt_on])
                        elif minor >= 3 and pwr_idx is ICE.POWER_GOC:
                            self.power_goc_on = bool(ord(msg[2]))
                            logger.info("Set GOC circuit %s", ('off','on')[self.power_goc_on])
                        else:
                            logger.error("Illegal power index: %d", pwr_idx)
                            raise Exception
                        self.ack()
                    else:
                        logger.error("bad 'p' subtype: " + msg[0])
                        raise UnknownCommandException
                else:
                    logger.error("Unknown msg type: " + msg_type)
                    raise UnknownCommandException
            except UnknownCommandException:
                self.nak()
            except NameError:
                logger.error("Commands issued before version negotiation?")
                raise
            except serial.SerialException:
                logger.error("Serial Port closed on other end")
                break
            except KeyboardInterrupt:
                for th in threading.enumerate():
                    print(th)
                    traceback.print_stack(sys._current_frames()[th.ident])
                    print('------------------')
                raise

    def respond(self, msg, ack=True):
        with self.s_lock:
            if (ack):
                self.s.write(bytes((0,)))
            else:
                self.s.write(bytes((1,)))
            self.s.write(bytes((self.event,)))
            self.event += 1
            self.event %= 256
            self.s.write(bytes((len(msg),)))

            if type(msg) != bytes:
                msg = bytes(msg, 'utf-8')

            if len(msg):
                self.s.write(msg)
        logger.debug("Sent a response of length: " + str(len(msg)))

    def ack(self):
        self.respond('')

    def nak(self):
        self.respond('', ack=False)


    @staticmethod
    def get_parser():
        parser = argparse.ArgumentParser()

        parser.add_argument("-i", "--ice-version", default=4, type=int, help="Maximum ICE Version to emulate (1, 2, or 3)")
        parser.add_argument("-s", "--serial", default=_FAKE_SERIAL_SIMULATOR_ENDPOINT, help="Serial port to connect to")
        parser.add_argument("-S", "--suppress-fake-serial", action='store_false', help="Do not create a software serial tunnerl")
        parser.add_argument("--i2c-mask", default=DEFAULT_I2C_MASK, help="Address mask for fake_ice i2c address")
        parser.add_argument("-a", "--ack-all", action="store_true", help="Only supports i2c at the moment")
        parser.add_argument("-g", "--generate-messages", action="store_true", help="Generate periodic, random MBus messages")
        parser.add_argument("-r", "--replay", default=None, help="Replay a ICE snoop trace")
        parser.add_argument('-t', '--transaction', default=None, 
            help='Enter transaction mode to replay a series of ICE messages with timing')

        return parser

    def parse_cli(self):
        self.args = Simulator.get_parser().parse_args()


    def match_mask(self, val, ones, zeros):
        if self.args.ack_all:
            return True
        return ((val & ones) == ones) and ((~val & zeros) == zeros)



    def sleep(self, *args, **kwargs):
        if not hasattr(self, '_sleep'):
            try:
                os.environ['ICE_NOSLEEP']
                self._sleep = lambda x: None
            except KeyError:
                self._sleep = time.sleep
        self._sleep(*args, **kwargs)




class Gpio(object):
    GPIO_INPUT    = 0
    GPIO_OUTPUT   = 1
    GPIO_TRISTATE = 2

    def __init__(self, direction=GPIO_INPUT, level=False, interrupt=False):
        self.direction = direction
        self.level = level
        self.interrupt = interrupt

    def __str__(self):
        s = ''
        if self.direction == Gpio.GPIO_INPUT:
            s += ' IN'
        elif self.direction == Gpio.GPIO_OUTPUT:
            s += 'OUT'
        elif self.direction == Gpio.GPIO_TRISTATE:
            s += 'TRI'
        else:
            raise RuntimeError("impossible gpio direction")

        s += ' - '

        if self.level:
            s += '1'
        else:
            s += '0'

        if self.interrupt:
            s += '(int_en)'

        return s

    def __repr__(self):
        return self.__str__()

    def __setattr__(self, name, value):
        if name is 'direction':
            if value not in (Gpio.GPIO_INPUT, Gpio.GPIO_OUTPUT, Gpio.GPIO_TRISTATE):
                raise ValueError("Attempt to set illegal direction {}".format(value))
        if name is 'level':
            if value not in (True, False):
                raise ValueError("GPIO level must be true or false. Got {}".format(value))
        if name is 'interrupt':
            if value not in (True, False):
                raise ValueError("GPIO interrupt must be true or false. Got {}".format(value))
        object.__setattr__(self, name, value)


_socat_time = str(datetime.datetime.now())
_socat_fpre = os.path.join(tempfile.gettempdir(), _socat_time + '-')
_socat_proc = None
_socat_devnull = None

if platform.system() == 'Darwin':
    # Well-intentioned private temp directories are annoying in this case
    tempfile.tempdir = '/tmp'
_FAKE_SERIAL_CONNECTTO_ENDPOINT = os.path.join(tempfile.gettempdir(), 'm3_ice_com1')
_FAKE_SERIAL_SIMULATOR_ENDPOINT = os.path.join(tempfile.gettempdir(), 'm3_ice_com2')

def destroy_fake_serial():
    global _socat_proc
    global _socat_devnull

    if _socat_proc:
        _socat_proc.terminate()
        _socat_proc.wait()
        _socat_proc = None
    if _socat_devnull:
        _socat_devnull.close()
        _socat_devnull = None

def create_fake_serial(
        endpoint1=_FAKE_SERIAL_CONNECTTO_ENDPOINT,
        endpoint2=_FAKE_SERIAL_SIMULATOR_ENDPOINT,
        ):
    global _socat_fpre
    global _socat_proc
    global _socat_devnull

    _socat_devnull = open(os.devnull, 'w')
    _socat_proc = subprocess.Popen(
                "socat -x pty,link={},raw,echo=0 pty,link={},raw,echo=0".format(endpoint1, endpoint2),
                stdout=open(_socat_fpre + 'socat-stdout', 'w'),
                stderr=open(_socat_fpre + 'socat-stderr', 'w'),
                shell=True,
                )

    # Hack, b/c socat doesn't exit but do need to wait for pipe to be set up
    limit = time.time() + 5
    while not (os.path.exists(endpoint1) and os.path.exists(endpoint2)):
        time.sleep(.1)
        if time.time() > limit:
            _socat_proc.kill()
            for l in open(_socat_fpre + 'socat-stdout'):
                logger.debug(l)
            for l in open(_socat_fpre + 'socat-stderr'):
                logger.debug(l)
            raise NotImplementedError("socat endpoint never appeared?")

    logger.debug("Fake serial bridge created.")

    atexit.register(destroy_fake_serial)


def cmd():
    try:
        Simulator().run()
    except KeyboardInterrupt:
        logger.info("Caught quit request. Shutting down.")

if __name__ == '__main__':
    Simulator().run()


