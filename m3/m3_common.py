#!/usr/bin/env python

from __future__ import print_function

import argparse
import atexit
import csv
import inspect
import os
import sys
import socket
import Queue
import time
import threading

# if Py2K:
import imp

from m3_logging import get_logger
logger = get_logger(__name__)
logger.debug('Got m3_common.py logger')

from ice import ICE
from ice_simulator import _FAKE_SERIAL_CONNECTTO_ENDPOINT

# Do this after ICE since ICE prints a nice help if pyserial is missing
import serial.tools.list_ports

def printing_sleep(seconds):
    try:
        os.environ['ICE_NOSLEEP']
        return
    except KeyError:
        pass
    if seconds < 1:
        time.sleep(seconds)
        return
    while (seconds > 0):
        sys.stdout.write("\rSleeping %d s" % (int(seconds)) + ' '*20)
        sys.stdout.flush()
        time.sleep(min(1, seconds))
        seconds -= 1
    sys.stdout.write('\r' + ' '*80 + '\r')
    sys.stdout.flush()

class m3_common(object):
    TITLE = "Generic M3 Programmer"
    DESCRIPTION = None
    EPILOG = None

    def default_value(self, prompt, default, extra=None, invert=False):
        if invert and (extra is None):
            raise RuntimeError, "invert & !extra ?"
        if self.args.yes:
            fn = print
        else:
            fn = raw_input
        if extra:
            r = fn(prompt + ' [' + default + extra + ']: ')
        else:
            r = fn(prompt + ' [' + default + ']: ')
        if self.args.yes:
            if invert:
                print("Chose {}".format(extra))
                return extra
            print("Chose {}".format(default))
            return default
        if len(r) == 0:
            if invert:
                return extra
            return default
        else:
            return r

    def do_default(self, prompt, fn, else_fn=None):
        y = self.default_value(prompt, 'Y', '/n')
        if y[0] not in ('n', 'N'):
            fn()
        else:
            if else_fn:
                else_fn()

    def dont_do_default(self, prompt, fn, else_fn=None):
        resp = self.default_value(prompt, 'y/', 'N', invert=True)
        if resp[0] in ('y', 'Y'):
            fn()
        else:
            if else_fn:
                else_fn()

    @staticmethod
    def _build_injection_message(
            # Byte 0: Control
            chip_id_mask=None,          # [0:3] Chip ID Mask
            reset_request=0,            #   [4] Reset Request
            chip_id_coding=0,           #   [5] Chip ID coding
            is_mbus=0,                  #   [6] Indicates transmission is MBus message [addr+data]
            run_after=False,            #   [7] Run code after programming?

            # Byte 1,2: Chip ID
            chip_id = 0,

            # Byte 3,4: Memory Address
            memory_address=0,

            # Data to send
            hexencoded_data=None,

            # GOC Version
            goc_version=0,              # Illegal version by default
            ):
        if goc_version not in (1,2,3):
            raise NotImplementedError("Bad GOC Version?")

        if chip_id_mask is None:
            if goc_version == 1:
                chip_id_mask = 0
            elif goc_version in (2,3):
                chip_id_mask = 0xF

        HEADER = ''

        # Control Byte
        control = chip_id_mask |\
                (reset_request << 4) |\
                (chip_id_coding << 5) |\
                (is_mbus << 6) |\
                (run_after << 7)
        HEADER += "%02X" % (control)

        # Chip ID
        HEADER += "%04X" % (chip_id)

        # Memory Address
        if goc_version == 1:
            HEADER += "%04X" % (memory_address)

        # Program Lengh
        if hexencoded_data is not None:
            length = len(hexencoded_data) >> 3   # hex exapnded -> bytes, /2
            if goc_version in (2,3):
                length -= 1
                assert length >= 0
            length = socket.htons(length)
        else:
            length = 0
        HEADER += "%04X" % (length)

        # Bit-wise XOR parity of header
        header_parity = 0
        for byte in [HEADER[x:x+2] for x in xrange(0, len(HEADER), 2)]:
            byte = int(byte, 16)
            if goc_version in (1,2):
                header_parity ^= byte
            elif goc_version == 3:
                header_parity = (header_parity + byte) & 0xFF
        HEADER += "%02X" % (header_parity)

        DATA = ''
        if hexencoded_data is not None:
            if goc_version in (2,3):
                DATA += "%08X" % (memory_address)

            DATA += hexencoded_data

            # Bit-wise XOR parity of data
            data_parity = 0
            for byte in [DATA[x:x+2] for x in xrange(0, len(DATA), 2)]:
                b = int(byte, 16)
                if goc_version in (1,2):
                    data_parity ^= b
                elif goc_version == 3:
                    data_parity = (data_parity + b) & 0xFF

            if goc_version == 1:
                DATA = '%02X' % (data_parity) + DATA
            else:
                DATA += '%02X' % (data_parity)

        return HEADER + DATA

    @staticmethod
    def build_injection_message_for_goc_v1(**kwargs):
        return m3_common._build_injection_message(goc_version=1, **kwargs)

    @staticmethod
    def build_injection_message_for_goc_v2(**kwargs):
        return m3_common._build_injection_message(goc_version=2, **kwargs)

    @staticmethod
    def build_injection_message_for_goc_v3(**kwargs):
        return m3_common._build_injection_message(goc_version=3, **kwargs)

    @staticmethod
    def build_injection_message_interrupt_for_goc_v1(hexencoded, run_after=True):
        return m3_common.build_injection_message(
                hexencoded_data=hexencoded,
                run_after=run_after,
                memory_address=0x1A00,
                )

    @staticmethod
    def build_injection_message_interrupt_for_goc_v2(hexencoded, run_after=True):
        return m3_common.build_injection_message_for_goc_v2(
                hexencoded_data=hexencoded,
                run_after=run_after,
                memory_address=0x1E00,
                )

    @staticmethod
    def build_injection_message_interrupt_for_goc_v3(hexencoded, run_after=True):
        return m3_common.build_injection_message_for_goc_v3(
                hexencoded_data=hexencoded,
                run_after=run_after,
                memory_address=0x1E00,
                )

    @staticmethod
    def build_injection_message_custom(mem_addr_custom, hexencoded, run_after):
        return m3_common.build_injection_message(
                hexencoded_data=hexencoded,
                run_after=run_after,
                memory_address=mem_addr_custom,
                )

    @staticmethod
    def build_injection_message_mbus(mbus_addr, mbus_data, run_after=False):
        chip_id_mask = 0                # [0:3] Chip ID Mask
        reset = 0                       #   [4] Reset Request
        chip_id_coding = 0              #   [5] Chip ID coding
        is_i2c = 1                      #   [6] Indicates transmission is I2C message [addr+data]
        run_after = not not run_after   #   [7] Run code after programming?
        # Byte 0: Control
        control = chip_id_mask | (reset << 4) | (chip_id_coding << 5) | (is_i2c << 6) | (run_after << 7)

        # Byte 1,2: Chip ID
        chip_id = 0

        # Byte 3,4,5,6: MBus Address
        i2c_addr = mbus_addr

        # Byte 7,8,9,10: MBus Data
        i2c_data = mbus_data

        # Byte 11: bit-wise XOR parity of header
        header_parity = 0
        for byte in (
                control,
                (chip_id >> 8) & 0xff,
                chip_id & 0xff,
                (i2c_addr >> 24) & 0xff,
                (i2c_addr >> 16) & 0xff,
                (i2c_addr >> 8) & 0xff,
                i2c_addr & 0xff,
                (i2c_data >> 24) & 0xff,
                (i2c_data >> 16) & 0xff,
                (i2c_data >> 8) & 0xff,
                i2c_data & 0xff,
                ):
            header_parity ^= byte

        # Assemble message:
        message = "%02X%04X%08X%08X%02X" % (
                control,
                chip_id,
                i2c_addr,
                i2c_data,
                header_parity)

        return message

    @staticmethod
    def build_reset_req_message():
        return m3_common.build_injection_message(
                hexencoded_data = "00000000",
                run_after = 0,
                memory_address = 0x000,
                reset_request = True,
                )

    @staticmethod
    def build_injection_message_mbus(mbus_addr, mbus_data, run_after=False):
        is_i2c = 1                      #   [6] Indicates transmission is I2C message [addr+data]
        # Byte 3,4,5,6: MBus Address
        i2c_addr = mbus_addr
        # Byte 7,8,9,10: MBus Data
        i2c_data = mbus_data

        # Byte 11: bit-wise XOR parity of header
        header_parity = 0
        for byte in (
                control,
                (chip_id >> 8) & 0xff,
                chip_id & 0xff,
                (i2c_addr >> 24) & 0xff,
                (i2c_addr >> 16) & 0xff,
                (i2c_addr >> 8) & 0xff,
                i2c_addr & 0xff,
                (i2c_data >> 24) & 0xff,
                (i2c_data >> 16) & 0xff,
                (i2c_data >> 8) & 0xff,
                i2c_data & 0xff,
                ):
            header_parity ^= byte

        # Assemble message:
        message = "%02X%04X%08X%08X%02X" % (
                control,
                chip_id,
                i2c_addr,
                i2c_data,
                header_parity)

        return message

    def __init__(self):
        self.wait_event = threading.Event()

        try:
            self.print_banner()
            self.parse_args()
            self.ice = ICE()
            self.callback_q = Queue.Queue()
            self.install_handler()
            self.ice.connect(self.serial_path)
            self.wakeup_goc_circuit()
        except NameError:
            logger.error("Abstract element missing.")
            raise

        atexit.register(self.exit_handler)

    def exit_handler(self):
        try:
            if self.args.wait_for_messages:
                self.hang_for_messages()
        except AttributeError:
            pass

    def wakeup_goc_circuit(self):
        # Fix an ICE issue where the power rails must be poked for
        # the GOC circuitry to wake up
        self.ice.goc_set_onoff(False)
        self.ice.power_set_onoff(self.ice.POWER_GOC, True)

    def install_handler(self):
        self.ice.msg_handler[self.MSG_TYPE] = self.callback_helper

    def callback_helper(self, msg_type, event_id, length, msg):
        logger.debug("Callback: msg len " + str(len(msg)))
        if len(msg) == 0:
            logger.debug("Ignore msg of len 0")
            return
        callback_q.put(msg)

    def print_banner(self):
        logger.info("-" * 80)
        logger.info(" -- " + self.TITLE)
        logger.info("")

    def add_parse_args(self):
        self.parser.add_argument('-s', "--serial",
                default=None,
                help="Path to ICE serial device")

        self.parser.add_argument('-w', '--wait-for-messages',
                action='store_true',
                help="Wait for messages (hang) when done.")

        self.parser.add_argument('-y', '--yes',
                action='store_true',
                help="Use default values for all prompts.")


    def parse_args(self):
        self.parser = argparse.ArgumentParser(
                formatter_class=argparse.ArgumentDefaultsHelpFormatter,
                description=self.DESCRIPTION,
                epilog=self.EPILOG,
                )
        self.add_parse_args()

        self.args = self.parser.parse_args()
        if self.args.serial is None:
            self.serial_path = self.guess_serial()
        else:
            self.serial_path = self.args.serial

        # XXX This is a bit of a hack
        if 'goc_version' in self.args:
            if self.args.goc_version == 1:
                self.build_injection_message = self.build_injection_message_for_goc_v1
                self.build_injection_message_interrupt = self.build_injection_message_interrupt_for_goc_v1
            elif self.args.goc_version == 2:
                self.build_injection_message = self.build_injection_message_for_goc_v2
                self.build_injection_message_interrupt = self.build_injection_message_interrupt_for_goc_v2
            elif self.args.goc_version == 3:
                self.build_injection_message = self.build_injection_message_for_goc_v3
                self.build_injection_message_interrupt = self.build_injection_message_interrupt_for_goc_v3
            else:
                raise NotImplementedError("Bad GOC version?")

    @staticmethod
    def get_serial_candidates():
        candidates = []
        for s in serial.tools.list_ports.comports():
            s = s[0]
            if 'bluetooth' in s.lower():
                continue
            candidates.append(s)
        # In many cases when debugging, we'll be using the fake_ice at '/tmp/com1'
        if os.path.exists(_FAKE_SERIAL_CONNECTTO_ENDPOINT):
            candidates.append(_FAKE_SERIAL_CONNECTTO_ENDPOINT)
        return candidates

    def guess_serial(self):
        candidates = self.get_serial_candidates()
        if len(candidates) == 0:
            logger.error("Could not find the serial port ICE is attached to.\n")
            self.parser.print_help()
            sys.exit(1)
        elif len(candidates) == 1:
            logger.info("Guessing ICE is at: " + candidates[0])
            return candidates[0]
        else:
            def pick_serial():
                logger.info("Multiple possible serial ports found:")
                for i in xrange(len(candidates)):
                    logger.info("\t[{}] {}".format(i, candidates[i]))
                try:
                    resp = raw_input("Choose a serial port (Ctrl-C to quit): ").strip()
                except KeyboardInterrupt:
                    sys.exit(1)
                try:
                    return candidates[int(resp)]
                except:
                    logger.info("Please choose one of the available serial ports.")
                    return pick_serial()
            return pick_serial()

    @staticmethod
    def read_binfile_static(binfile):
        def guess_type_is_hex(binfile):
            for line in open(binfile):
                for c in line.strip():
                    c = ord(c)
                    if c < 0x20 or c > 0x7a:
                        return False
            return True

        if guess_type_is_hex(binfile):
            binfd = open(binfile, 'r')
            hexencoded = ""
            for line in binfd:
                hexencoded += line[0:2].upper()
        else:
            binfd = open(binfile, 'rb')
            hexencoded = binfd.read().encode("hex").upper()

        if (len(hexencoded) % 4 == 0) and (len(hexencoded) % 8 != 0):
            # Image is halfword-aligned. Some tools generate these, but our system
            # assumes things are word-aligned. We pad an extra nop to the end to fix
            hexencoded += '46C0' # nop; (mov r8, r8)

        if (len(hexencoded) % 8) != 0:
            logger.warn("Binfile is not word-aligned. This is not a valid image")
            return None

        return hexencoded

    def read_binfile(self, binfile):
        self.hexencoded = m3_common.read_binfile_static(binfile)
        if self.hexencoded is None:
            sys.exit(3)

    def power_on(self, wait_for_rails_to_settle=True):
        logger.info("Turning all M3 power rails on")
        self.ice.power_set_voltage(0,0.6)
        self.ice.power_set_voltage(1,1.2)
        self.ice.power_set_voltage(2,3.8)
        logger.info("Turning 3.8 on")
        self.ice.power_set_onoff(2,True)
        if wait_for_rails_to_settle:
            printing_sleep(1.0)
        logger.info("Turning 1.2 on")
        self.ice.power_set_onoff(1,True)
        if wait_for_rails_to_settle:
            printing_sleep(1.0)
        logger.info("Turning 0.6 on")
        self.ice.power_set_onoff(0,True)
        if wait_for_rails_to_settle:
            printing_sleep(1.0)
            logger.info("Waiting 8 seconds for power rails to settle")
            printing_sleep(8.0)

    def reset_m3(self):
        logger.info("M3 0.6V => OFF (reset controller)")
        self.ice.power_set_onoff(0,False)
        printing_sleep(2.0)
        logger.info("M3 0.6V => ON")
        self.ice.power_set_onoff(0,True)
        printing_sleep(2.0)

    def hang_for_messages(self):
        logger.info("Script is waiting to print any MBus messages.")
        logger.info("To quit, press Ctrl-C")
        try:
            while not self.wait_event.wait(1000):
                pass
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt.")

    def exit(self):
        self.wait_event.set()
        sys.exit()


class goc_programmer(object):
    TITLE = "GOC Programmer"
    #SLOW_FREQ_IN_HZ = 0.625
    SLOW_FREQ_IN_HZ = 70

    def __init__(self, m3_ice, parser):
        self.m3_ice = m3_ice
        self.parser = parser
        self.add_parse_args(parser)

    def add_parse_args(self, parser):
        parser.add_argument('-g', '--goc-speed',
                help="GOC Slow Speed in Hz. The fast speed will be 8x faster."\
                        " Defaults to " + str(goc_programmer.SLOW_FREQ_IN_HZ) + " Hz.",
                        default=goc_programmer.SLOW_FREQ_IN_HZ, type=float)

        parser.add_argument('-V', '--goc-version',
                help="GOC protocol version. Defaults to 2",
                default=2,
                type=int,
                )

        parser.add_argument('-d', '--delay',
                help="Delay (in seconds) between passcode and message. Default: 1s.",
                default=1,
                type=float,
                )

        self.subparsers = parser.add_subparsers(
                title='GOC Commands',
                description='GOC Actions supported by the ICE board',
                )

        self.parser_on = self.subparsers.add_parser('on',
                help="Turn GOC light on")
        self.parser_on.set_defaults(func=self.cmd_on)

        self.parser_off = self.subparsers.add_parser('off',
                help="Turn GOC light off")
        self.parser_off.set_defaults(func=self.cmd_off)

        self.parser_message = self.subparsers.add_parser('message',
                help="Send mbus message")
        self.parser_message.add_argument('ADDRESS',
                help="Address to send to as a hex string (e.g. a5)")
        self.parser_message.add_argument('MESSAGE',
                help="Message to send as a hex string (e.g. 12345678)")
        self.parser_message.add_argument('-R', '--dont-run-after',
                action='store_true',
                help="By default, message commands set the run_after bit. Unset it")
        self.parser_message.set_defaults(func=self.cmd_message)

        self.parser_flash = self.subparsers.add_parser('flash',
                help="Flash program image")
        self.parser_flash.add_argument('BINFILE', help="Program to flash")
        self.parser_flash.set_defaults(func=self.cmd_flash)

        # goc.py goc_generate_mbus_message.py goc_off.py goc_to_image.py goc_v2.py

    def cmd_on(self):
        self.m3_ice.ice.goc_set_onoff(True)

    def cmd_off(self):
        self.m3_ice.ice.goc_set_onoff(False)

    def _generic_startup(self):
        self.m3_ice.dont_do_default("Run power-on sequence", self.m3_ice.power_on)
        self.m3_ice.dont_do_default("Reset M3", self.m3_ice.reset_m3)
        logger.info("** Setting ICE MBus controller to slave mode")
        self.m3_ice.ice.mbus_set_master_onoff(False)

        self.set_slow_frequency()
        self.wake_chip()
        if self.m3_ice.args.delay:
            logger.info("Delaying {}s after passcode".format(self.m3_ice.args.delay))
            printing_sleep(self.m3_ice.args.delay)
        self.set_fast_frequency()

    def cmd_message(self):
        addr = self.m3_ice.args.ADDRESS
        addr = addr.replace('0x', '')
        # Flip the order of addr bytes to make human entry friendly
        # TODO: The encode/decode at various points is a bit silly?
        addr = addr.decode('hex')
        addr = addr[::-1]
        addr = addr.encode('hex')
        addr = int(addr, 16)

        data = self.m3_ice.args.MESSAGE
        data = data.replace('0x', '')
        if len(data) % 2 == 1:
            data = '0' + data

        # Flip the order of data bytes
        # TODO: The encode/decode at various points is a bit silly?
        data = data.decode('hex')
        data = data[::-1]
        data = data.encode('hex')

        if self.m3_ice.args.dont_run_after:
            run_after = False
        else:
            run_after = True

        message = self.m3_ice.build_injection_message(
                memory_address=addr,
                hexencoded_data=data,
                run_after=run_after,
                )

        self._generic_startup()

        logger.debug("Sending: " + message)
        self.send_goc_message(message)

        logger.info("")
        logger.info("Message sent.")

    def cmd_flash(self):
        self.m3_ice.read_binfile(self.m3_ice.args.BINFILE)

        logger.info("")
        logger.info("Would you like to run after programming? If you do not")
        logger.info("have GOC start the program, you will be prompted to send")
        logger.info("the start message via GOC/MBus at the end instead")
        logger.info("")
        self.m3_ice.run_after = False
        self.m3_ice.do_default("Run program when programming finishes?",
                lambda: setattr(self.m3_ice, 'run_after', True))

        self._generic_startup()

        message = self.m3_ice.build_injection_message(hexencoded_data=self.m3_ice.hexencoded, run_after=self.m3_ice.run_after)
        logger.debug("Sending: " + message)
        self.send_goc_message(message)

        logger.info("")
        logger.info("Programming complete.")
        logger.info("")

        if self.m3_ice.run_after:
            logger.info("Program is running on the chip")
        else:
            self.m3_ice.do_default("Would you like to read back the program to validate?", self.validate_bin)
            self.m3_ice.do_default("Would you like to send the DMA start interrupt?", self.DMA_start_interrupt)

    def set_slow_frequency(self):
        self.m3_ice.ice.goc_set_frequency(self.m3_ice.args.goc_speed)

    def wake_chip(self):
        passcode_string = "7394"
#        passcode_string = "3935"   # Reset request
        logger.info("Sending passcode to GOC")
        logger.debug("Sending:" + passcode_string)
        self.m3_ice.ice.goc_send(passcode_string.decode('hex'))
        printing_sleep(0.5)

    def set_fast_frequency(self):
        self.m3_ice.ice.goc_set_frequency(8*self.m3_ice.args.goc_speed)

    def send_goc_message(self, message):
        logger.info("Sending GOC message")
        logger.debug("Sending: " + message)
        self.m3_ice.ice.goc_send(message.decode('hex'))
        printing_sleep(0.5)

        logger.info("Sending extra blink to end transaction")
        extra = "80"
        logger.debug("Sending: " + extra)
        self.m3_ice.ice.goc_send(extra.decode('hex'))

    def validate_bin(self):
        raise NotImplementedError("If you need this, let me know")

    def DMA_start_interrupt(self):
        raise NotImplementedError("If you need this, let me know")
        #logger.info("Sending 0x88 0x00000000")
        #self.send("88".decode('hex'), "00000000".decode('hex'))


class ein_programmer(object):
    TITLE = "EIN Programmer"
    DESCRIPTION = "Tool to program M3 chips using the EIN protocol."
    MSG_TYPE = 'b+'

    @staticmethod
    def add_parse_args(parser):
        parser.add_argument("BINFILE", help="Program to flash")


    def __init__(self, m3_ice):
        self.m3_ice = m3_ice
        self.m3_ice.read_binfile(self.m3_ice.args.BINFILE)

    def cmd(self):
        self.m3_ice.dont_do_default("Run power-on sequence", self.m3_ice.power_on)
        self.m3_ice.dont_do_default("Reset M3", self.m3_ice.reset_m3)
        logger.info("** Setting ICE MBus controller to slave mode")
        self.m3_ice.ice.mbus_set_master_onoff(False)

        logger.info("")
        logger.info("Would you like to run after programming? If you do not")
        logger.info("have EIN Debug start the program, you will be prompted")
        logger.info("to send the start message via MBus at the end instead")
        logger.info("")
        self.m3_ice.run_after = False
        self.m3_ice.do_default("Run program when programming finishes?",
                lambda: setattr(self.m3_ice, 'run_after', True))

        message = self.m3_ice.build_injection_message(hexencoded_data=self.m3_ice.hexencoded, run_after=self.m3_ice.run_after)
        logger.debug("Sending: " + message)
        self.m3_ice.ice.ein_send(message.decode('hex'))

        logger.info("")
        logger.info("Programming complete.")
        logger.info("")

        if self.m3_ice.run_after:
            logger.info("Program is running on the chip")
        else:
            self.m3_ice.do_default("Would you like to read back the program to validate?", self.validate_bin)
            self.m3_ice.do_default("Would you like to send the DMA start interrupt?", self.DMA_start_interrupt)

    def DMA_start_interrupt(self):
        logger.info("Sending 0x88 0x00000000")
        self.m3_ice.ice.mbus_send("88".decode('hex'), "00000000".decode('hex'))

    def validate_bin(self): #, hexencoded, offset=0):
        raise NotImplementedError("Need to update for MBus. Let me know if needed.")
        logger.info("Configuring ICE to ACK adress 1001 100x")
        ice.i2c_set_address("1001100x") # 0x98

        logger.info("Running Validation sequence:")
        logger.info("\t DMA read at address 0x%x, length %d" % (offset, len(hexencoded)/2))
        logger.info("\t<Receive I2C message for DMA data>")
        logger.info("\tCompare received data and validate it was programmed correctly")

        length = len(hexencoded)/8
        offset = offset
        data = 0x80000000 | (length << 16) | offset
        dma_read_req = "%08X" % (socket.htonl(data))
        logger.debug("Sending: " + dma_read_req)
        ice.i2c_send(0xaa, dma_read_req.decode('hex'))

        logger.info("Chip Program Dump Response:")
        chip_bin = validate_q.get(True, ice.ONEYEAR)
        logger.debug("Raw chip bin response len " + str(len(chip_bin)))
        chip_bin = chip_bin.encode('hex')
        logger.debug("Chip bin len %d val: %s" % (len(chip_bin), chip_bin))

        #1,2-addr ...
        chip_bin = chip_bin[2:]

        # Consistent capitalization
        chip_bin = chip_bin.upper()
        hexencoded = hexencoded.upper()

        for b in range(len(hexencoded)):
            try:
                if hexencoded[b] != chip_bin[b]:
                    logger.warn("ERR: Mismatch at half-byte" + str(b))
                    logger.warn("Expected:" + hexencoded[b])
                    logger.warn("Got:" + chip_bin[b])
                    return False
            except IndexError:
                logger.warn("ERR: Length mismatch")
                logger.warn("Expected %d bytes" % (len(hexencoded)/2))
                logger.warn("Got %d bytes" % (len(chip_bin)/2))
                logger.warn("All prior bytes validated correctly")
                return False

        logger.info("Programming validated successfully")
        return True


class mbus_snooper(object):
    TITLE = "MBus Snooper"
    DEFAULT_SNOOP_PREFIX="0111"

    @staticmethod
    def add_parse_args(parser):
        parser.add_argument('-p', '--short-prefix',
                help="Only snoop messages that match prefix, x for don't care, e.g. 011x",
                default=mbus_snooper.DEFAULT_SNOOP_PREFIX,
                )

        parser.add_argument('-t', '--message-timeout',
                help="Reset ICE if no messages are heard after X seconds [0 to disable]",
                type=int,
                default=10,
                )

        parser.add_argument('-P', '--no-print',
                help="Do not print snooped messages to the screen",
                action="store_true",
                )

        parser.add_argument('--csv',
                help="Save snooped messages to csv file",
                default=None,
                )

        parser.add_argument('-c', '--callback',
                help="Custom Python function to run. Argument must be a valid Python file with a top-level function of the form 'def callback(_time, address, data, cb0, cb1):'. The flag may be supplied multiple times to provide multiple callback functions.",
                action='append',
                )

    def _callback(self, *args, **kwargs):
        self.reset_event.set()
        self._callback_queue.put((time.time(), args, kwargs))

    def _callback_runner(self):
        while True:
            time, args, kwargs = self._callback_queue.get()
            if len(self.callbacks) == 0:
                logger.warn("No callbacks registered. Message dropped.")
            for callback in self.callbacks:
                callback(time, *args, **kwargs)

    def callback_print(self, _time, address, data, cb0, cb1):
        print("@ Time: " + time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_time)) + "  ADDR: 0x" + address.encode('hex') + "  DATA: 0x" + data.encode('hex') + "  (ACK: " + str(not cb1) + ")")

    def callback_csv(self, _time, address, data, cb0, cb1):
        self._csv_writer.writerow((_time, address.encode('hex'), data.encode('hex'), cb0, cb1))

    def __init__(self, args, ice, callbacks=None):
        self.args = args
        self.ice = ice

        self.callbacks = []

        if not self.args.no_print:
            self.callbacks.append(self.callback_print)

        if self.args.csv is not None:
            self._csv_file = open(self.args.csv, 'wb')
            self._csv_writer = csv.writer(self._csv_file)
            self.callbacks.append(self.callback_csv)

        for idx,callback in enumerate(self.args.callback):
            # n.b. Py2K only
            # http://stackoverflow.com/questions/67631/how-to-import-a-module-given-the-full-path
            mod = imp.load_source('custom_cb.cb'+str(idx), callback)
            try:
                self.callbacks.append(mod.callback)
            except AttributeError:
                logger.error("Did you define a function named exactly 'callback' ?")
                raise

        if callbacks:
            self.callbacks.extend(callbacks)

        self._callback_queue = Queue.Queue()
        self._callback_thread = threading.Thread(target=self._callback_runner)
        self._callback_thread.daemon = True
        self._callback_thread.start()

        self.ice.B_formatter_control_bits = True
        self.ice.msg_handler['B++'] = self._callback
        self.ice.msg_handler['b++'] = self._callback

        self.ice.ice_set_baudrate_to_2000000()
        def _atexit_reset_baudrate():
            self.ice.ice_set_baudrate_to_115200()
        atexit.register(_atexit_reset_baudrate)

        self.ice.mbus_set_internal_reset(True)
        self.ice.mbus_set_master_onoff(False)
        self.ice.mbus_set_snoop(True)
        self.ice.mbus_set_short_prefix(self.args.short_prefix)
        self.ice.mbus_set_internal_reset(False)

        if self.args.message_timeout != 0:
            self.reset_event = threading.Event()

            def reset_mbus():
                while True:
                    if self.reset_event.wait(10):
                        self.reset_event.clear()
                    else:
                        logger.warn("No messages for 10 seconds, resetting ICE")
                        self.ice.mbus_set_internal_reset(True)
                        self.ice.mbus_set_internal_reset(False)
                        self.ice.mbus_set_internal_reset(True)
                        self.ice.mbus_set_internal_reset(False)

            self.reset_thread = threading.Thread(target=reset_mbus)
            self.reset_thread.daemon = True
            self.reset_thread.start()


