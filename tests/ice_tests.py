#!/usr/bin/env python

import datetime
import os
import sys
import struct
import socket
import tempfile

import logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger('program')

import threading
import subprocess

import inspect

from m3.ice import ICE
import m3.ice_simulator

class TestICE(object):
    class TestFailedException(Exception):
        pass

    @classmethod
    def setup_class(cls, serial_port=m3.ice_simulator._FAKE_SERIAL_CONNECTTO_ENDPOINT):
        cls.sim_thread = None

        os.environ['ICE_NOSLEEP'] = '1'
        sim_args = m3.ice_simulator.Simulator.get_parser().parse_args([])
        cls.sim_thread = threading.Thread(
                target=m3.ice_simulator.Simulator(args=sim_args).run,
                name='fake_ice',
                )
        cls.sim_thread.daemon = True
        cls.sim_thread.start()

        cls.ice = ICE()
        cls.ice.connect(serial_port)

    @classmethod
    def teardown_class(cls):
        m3.ice_simulator.destroy_fake_serial()

    def test_query_capabilities(self):
        logger.info("Test ??")
        caps = TestICE.ice.ice_query_capabilities()
        logger.debug("ICE Capability String: " + caps)

    def test_baudrate(self):
        if sys.platform.lower() == 'darwin':
            logger.warn("test_baudrate skipped on OS X")
            return
        logger.info("Test ?b")
        TestICE.ice.ice_set_baudrate_to_3_megabaud()
        baud = TestICE.ice.ice_get_baudrate()
        if baud != 3000000:
            logger.error("Set/get baudrate mismatch")
            logger.error("Expected 3000000, got " + str(baud))
            raise self.TestFailedException

    def test_discrete_i2c(self):
        logger.info("Test d")
        ret = TestICE.ice.i2c_send(0xa5, "12345678".decode('hex'))
        if ret != 5:
            logger.error("Failed to send whole short I2C message")
            logger.info("  Did you set fake_TestICE.ice to ACK all addresses?")
            logger.info("  That is: ./fake_TestICE.ice.py /tmp/com2 xxxxxxxx")
        ret = TestICE.ice.i2c_send(0x69, ("ab"*511).decode('hex'))
        if ret != (1+511):
            logger.error("Failed to send whole long I2C message")

    def test_i2c_speed(self):
        logger.info("Test ic")
        TARGET_SPEED = 80
        TestICE.ice.i2c_set_speed(TARGET_SPEED)
        speed = TestICE.ice.i2c_get_speed()
        if speed != TARGET_SPEED:
            logger.error("Set/Get speed mismatch")

    def test_i2c_address(self):
        logger.info("Test ia")
        TARGET_ADDRESS = "1x01010x"
        TestICE.ice.i2c_set_address(TARGET_ADDRESS)
        address = TestICE.ice.i2c_get_address()
        if address != TARGET_ADDRESS:
            logger.error("Set/get mismatch i2c address")
            logger.error("Expected: " + TARGET_ADDRESS + "  Got: " + address)

    def test_goc(self):
        logger.info("Test f")
        ret = TestICE.ice.goc_send("a5".decode('hex'), show_progress=False)
        if ret != 1:
            logger.error("Failed to send whole short GOC message")
        ret = TestICE.ice.goc_send(("96"+"ba"*511).decode('hex'), show_progress=False)
        if ret != (1+511):
            logger.error("Failed to send whole long GOC message")

    def test_goc_speed(self):
        logger.info("Test oc")
        TARGET_FREQ = 12
        TestICE.ice.goc_set_frequency(TARGET_FREQ)
        freq = TestICE.ice.goc_get_frequency()
        if freq != TARGET_FREQ:
            logger.error("Set/get mismatch on GOC frequency")

    def test_goc_onoff(self):
        logger.info("Test oo")
        TestICE.ice.goc_set_onoff(True)
        onoff = TestICE.ice.goc_get_onoff()
        if onoff != True:
            logger.error("Set/get mismatch on GOC onoff")

    def test_mbus_message(self):
        logger.info("Test b")
        ret = TestICE.ice.mbus_send("5a".decode('hex'), "87654321".decode('hex'))
        # ret value from addr is always 4
        if ret != 8:
            logger.error("Failed to send whole short MBus message")
        ret = TestICE.ice.mbus_send("69".decode('hex'), ("ab"*511).decode('hex'))
        if ret != (4+511):
            logger.error("Failed to send whole long MBus message")

    #def test_mbus_full_prefix(self):
    #    logger.info("Test ml")
    #    TARGET_FULL_PREFIX = "10x0x0x11xx00xx1110x"
    #    TestICE.ice.mbus_set_full_prefix(TARGET_FULL_PREFIX)
    #    fp = TestICE.ice.mbus_get_full_prefix()
    #    if fp != TARGET_FULL_PREFIX:
    #        logger.error("Set/get mismatch mbus full prefix")

    #def test_mbus_short_prefix(self):
    #    logger.info("Test ms")
    #    TARGET_SHORT_PREFIX = "1x0x"
    #    TestICE.ice.mbus_set_short_prefix(TARGET_SHORT_PREFIX)
    #    sp = TestICE.ice.mbus_get_short_prefix()
    #    if sp != TARGET_SHORT_PREFIX:
    #        logger.error("Set/get mismatch mbus short prefix")

    #def test_mbus_full_snoop_prefix(self, ice):
    #    logger.info("Test mL")
    #    TARGET_FULL_SNOOP_PREFIX = "10xx0x110x1x01100x1x"
    #    ice.mbus_set_full_snoop_prefix(TARGET_FULL_SNOOP_PREFIX)
    #    fsp = ice.mbus_get_full_snoop_prefix()
    #    if fsp != TARGET_FULL_SNOOP_PREFIX:
    #        logger.error("Set/get mismatch mbus full snoop prefix")

    #def test_mbus_short_snoop_prefix(self, ice):
    #    logger.info("Test mS")
    #    TARGET_SHORT_SNOOP_PREFIX = "xx01"
    #    ice.mbus_set_short_snoop_prefix(TARGET_SHORT_SNOOP_PREFIX)
    #    ssp = ice.mbus_get_short_snoop_prefix()
    #    if ssp != TARGET_SHORT_SNOOP_PREFIX:
    #        logger.error("Set/get mismatch mbus short snoop prefix")

    def test_mbus_broadcast_mask(self):
        logger.info("Test mb")
        TARGET_BROADCAST_PREFIX = "1xx0"
        TestICE.ice.mbus_set_broadcast_channel_mask(TARGET_BROADCAST_PREFIX)
        bp = TestICE.ice.mbus_get_broadcast_channel_mask()
        if bp != TARGET_BROADCAST_PREFIX:
            logger.error("Set/get mismatch mbus broadcast mask")

    def test_mbus_broacast_snoop_mask(self):
        logger.info("Test mB")
        TARGET_BROADCAST_SNOOP_PREFIX = "x01x"
        TestICE.ice.mbus_set_broadcast_channel_snoop_mask(TARGET_BROADCAST_SNOOP_PREFIX)
        bsp = TestICE.ice.mbus_get_broadcast_channel_snoop_mask()
        if bsp != TARGET_BROADCAST_SNOOP_PREFIX:
            logger.error("Set/get mismatch mbus broadcast mask")

    def test_mbus_master_onoff(self):
        logger.info("Test mm")
        TestICE.ice.mbus_set_master_onoff(True)
        master = TestICE.ice.mbus_get_master_onoff()
        if master != True:
            logger.error("Set/get mismatch mbus master mode")

    def test_mbus_clock_speed(self):
        logger.info("Test mc")
        logger.warning("MBus clock setting not implemented -- skipping")
        return
        TARGET_FREQ = 600
        TestICE.ice.mbus_set_clock(TARGET_FREQ)
        clock = TestICE.ice.mbus_get_clock()
        if clock != TARGET_FREQ:
            logger.error("Set/get mismatch mbus clock")

    def test_mbus_should_interrupt(self):
        logger.info("Test mi")

        TestICE.ice.mbus_set_should_interrupt(1)
        i = TestICE.ice.mbus_get_should_interrupt()
        if i != 1:
            logger.error("Set/get mismatch mbus should int (1)")
            logger.error("Expected 1  Got " + str(i))
        TestICE.ice.mbus_send("ec".decode('hex'), "beef".decode('hex'))
        i = TestICE.ice.mbus_get_should_interrupt()
        if i != 0:
            logger.error("Should interrupt clear failed")

        TestICE.ice.mbus_set_should_interrupt(2)
        i = TestICE.ice.mbus_get_should_interrupt()
        if i != 2:
            logger.error("Set/get mismatch mbus should int (2)")
        TestICE.ice.mbus_send("ec".decode('hex'), "beef".decode('hex'))
        i = TestICE.ice.mbus_get_should_interrupt()
        if i != 2:
            logger.error("Should interrupt persistance failed")

    def test_mbus_use_priority(self):
        logger.info("Test mp")

        TestICE.ice.mbus_set_use_priority(1)
        i = TestICE.ice.mbus_get_use_priority()
        if i != 1:
            logger.error("Set/get mismatch mbus should int (1)")
        TestICE.ice.mbus_send("db".decode('hex'), "bead".decode('hex'))
        i = TestICE.ice.mbus_get_use_priority()
        if i != 0:
            logger.error("Should use_priority clear failed")

        TestICE.ice.mbus_set_use_priority(2)
        i = TestICE.ice.mbus_get_use_priority()
        if i != 2:
            logger.error("Set/get mismatch mbus should int (2)")
        TestICE.ice.mbus_send("ec".decode('hex'), "beef".decode('hex'))
        i = TestICE.ice.mbus_get_use_priority()
        if i != 2:
            logger.error("Should use_priority persistance failed")

    def test_ein(self):
        logger.info("Test e")
        ret = TestICE.ice.ein_send("a5".decode('hex'))
        if ret != 1:
            logger.error("Failed to send whole short EIN message")
        ret = TestICE.ice.ein_send(("96"+"ba"*511).decode('hex'))
        if ret != (1+511):
            logger.error("Failed to send whole long EIN message")

    def test_gpio_level(self):
        logger.info("Test gl")
        TestICE.ice.gpio_set_level(3, True)
        if TestICE.ice.gpio_get_level(3) != True:
            logger.error("Set/get mismatch gpio 3")
        TestICE.ice.gpio_set_level(6, False)
        if TestICE.ice.gpio_get_level(6) != False:
            logger.error("Set/get mismatch gpio 6")

    def test_gpio_direction(self):
        logger.info("Test gd")
        TestICE.ice.gpio_set_direction(1, TestICE.ice.GPIO_INPUT)
        if TestICE.ice.gpio_get_direction(1) != TestICE.ice.GPIO_INPUT:
            logger.error("Set/get mismatch gpio 1")
        TestICE.ice.gpio_set_direction(5, TestICE.ice.GPIO_OUTPUT)
        if TestICE.ice.gpio_get_direction(5) != TestICE.ice.GPIO_OUTPUT:
            logger.error("Set/get mismatch gpio 5")
        TestICE.ice.gpio_set_direction(1, TestICE.ice.GPIO_TRISTATE)
        if TestICE.ice.gpio_get_direction(1) != TestICE.ice.GPIO_TRISTATE:
            logger.error("Set/get mismatch gpio 1 (to tri)")

    def test_gpio_interrupt_mask(self):
        logger.info("Test gi")
        TARGET_GPIO_INT_MASK = 0xa53
        TestICE.ice.gpio_set_interrupt_enable_mask(TARGET_GPIO_INT_MASK)
        if TestICE.ice.gpio_get_interrupt_enable_mask != TARGET_GPIO_INT_MASK:
            logger.error("Set/get mismatch gpio interrupt mask")

    def test_voltage_state(self):
        logger.info("Test pv")
        TestICE.ice.power_set_voltage(TestICE.ice.POWER_0P6, 0.6)
        p = TestICE.ice.power_get_voltage(TestICE.ice.POWER_0P6)
        # power rail settings aren't round, resp is actual value, so check if
        # it's within ~2% of what we asked for (2% b/c 3.7 -> 1.1% error)
        if (abs(0.6 - p) / 0.6) > 0.02:
            logger.error("Set/get mismatch 0.6 V rail")
            logger.error("Expected 0.6  Got " + str(p))
        TestICE.ice.power_set_voltage(TestICE.ice.POWER_1P2, 1.2)
        p = TestICE.ice.power_get_voltage(TestICE.ice.POWER_1P2)
        if (abs(1.2 - p) / 1.2) > 0.02:
            logger.error("Set/get mismatch 1.2 V rail")
            logger.error("Expected 1.2  Got " + str(p))
        TestICE.ice.power_set_voltage(TestICE.ice.POWER_VBATT, 3.7)
        p = TestICE.ice.power_get_voltage(TestICE.ice.POWER_VBATT)
        if (abs(3.7 - p) / 3.7) > 0.02:
            logger.error("Set/get mismatch VBATT rail")
            logger.error("Expected 3.7  Got " + str(p))

    def test_power_onoff(self):
        logger.info("Test po")
        TestICE.ice.power_set_onoff(TestICE.ice.POWER_VBATT, True)
        if TestICE.ice.power_get_onoff(TestICE.ice.POWER_VBATT) != True:
            logger.error("Set/get mismatch VBATT power")
        TestICE.ice.power_set_onoff(TestICE.ice.POWER_1P2, True)
        if TestICE.ice.power_get_onoff(TestICE.ice.POWER_1P2) != True:
            logger.error("Set/get mismatch 1.2 V power")
        TestICE.ice.power_set_onoff(TestICE.ice.POWER_0P6, True)
        if TestICE.ice.power_get_onoff(TestICE.ice.POWER_0P6) != True:
            logger.error("Set/get mismatch 0.6 V power")



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
