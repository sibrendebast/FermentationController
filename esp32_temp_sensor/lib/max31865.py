# Micropython module for the MAX31865 platinum RTD temperature sensor.
# Adapted from the adafruit_max31865 CircuitPython library
# * Original Author(s): Tony DiCola
# * Modified by: Sufyan Mukadam
# Original Repo: https://github.com/adafruit/Adafruit_CircuitPython_MAX31865.git
# Released under the MIT License (MIT) - see LICENSE file

import math
import time

from micropython import const

from machine import Pin, SPI

# Register and other constant values:
_MAX31865_CONFIG_REG = const(0x00)
_MAX31865_CONFIG_BIAS = const(0x80)
_MAX31865_CONFIG_MODEAUTO = const(0x40)
_MAX31865_CONFIG_MODEOFF = const(0x00)
_MAX31865_CONFIG_1SHOT = const(0x20)
_MAX31865_CONFIG_3WIRE = const(0x10)
_MAX31865_CONFIG_24WIRE = const(0x00)
_MAX31865_CONFIG_FAULTSTAT = const(0x02)
_MAX31865_CONFIG_FILT50HZ = const(0x01)
_MAX31865_CONFIG_FILT60HZ = const(0x00)
_MAX31865_RTDMSB_REG = const(0x01)
_MAX31865_RTDLSB_REG = const(0x02)
_MAX31865_HFAULTMSB_REG = const(0x03)
_MAX31865_HFAULTLSB_REG = const(0x04)
_MAX31865_LFAULTMSB_REG = const(0x05)
_MAX31865_LFAULTLSB_REG = const(0x06)
_MAX31865_FAULTSTAT_REG = const(0x07)
_MAX31865_FAULT_HIGHTHRESH = const(0x80)
_MAX31865_FAULT_LOWTHRESH = const(0x40)
_MAX31865_FAULT_REFINLOW = const(0x20)
_MAX31865_FAULT_REFINHIGH = const(0x10)
_MAX31865_FAULT_RTDINLOW = const(0x08)
_MAX31865_FAULT_OVUV = const(0x04)
_RTD_A = 3.9083e-3
_RTD_B = -5.775e-7


class Max31865:
    """Driver for the MAX31865 RTD amplifier.

    :param ~machine.SPI spi: SPI device
    :param int cs: Chip Select Pin
    :param int rtd_nominal: RTD nominal value. Defaults to :const:`100`
    :param int ref_resistor: Reference resistance. Defaults to :const:`400.0`
    :param int wires: Number of wires. Defaults to :const:`2`
    :param int filter_frequency: . Filter frequency. Default to :const:`60`
    :param int polarity: set to 1 if controller clock idles high. Default 0.

    """

    def __init__(self, bus, cs, polarity=0, rtd_nominal=100,
                 ref_resistor=430.0, wires=2, filter_frequency=60,
                 misoPin=11, mosiPin=12, sckPin=10):

        # Setup CS
        if isinstance(cs, Pin):
            self.cs = cs
        else:
            self.cs = Pin(cs, mode=Pin.OUT)
        self.cs.value(1) # Default to high (inactive)

        # Setup SPI
        if isinstance(bus, SPI):
            self._device = bus
        else:
            self._device = SPI(bus, baudrate=200000, polarity=polarity, phase=1,
                               firstbit=SPI.MSB, sck=Pin(sckPin),
                               mosi=Pin(mosiPin), miso=Pin(misoPin))

        # Set Nominal and Reference Resistor Values
        self.rtd_nominal = rtd_nominal
        self.ref_resistor = ref_resistor

        self._BUFFER = bytearray(3)

        # Set 50Hz or 60Hz filter.
        if filter_frequency not in (50, 60):
            raise ValueError("Filter_frequency must be a value of 50 or 60!")
        config = self._read_u8(_MAX31865_CONFIG_REG)
        if filter_frequency == 50:
            config |= _MAX31865_CONFIG_FILT50HZ
        else:
            config &= ~_MAX31865_CONFIG_FILT50HZ

        # Set wire config register based on the number of wires specified.
        if wires not in (2, 3, 4):
            raise ValueError("Wires must be a value of 2, 3, or 4!")
        if wires == 3:
            config |= _MAX31865_CONFIG_3WIRE
        else:
            # 2 or 4 wire
            config &= ~_MAX31865_CONFIG_3WIRE
        self._write_u8(_MAX31865_CONFIG_REG, config)
        # Default to no bias and no auto conversion.
        self.bias = False
        self.auto_convert = False

    def _read_u8(self, address):
        # Read an 8-bit unsigned value from the specified 8-bit address.
        self.cs.value(0)
        # buffer = bytearray(3)
        self._BUFFER[0] = address & 0x7F
        self._device.write(self._BUFFER[0:1])
        self._device.readinto(self._BUFFER)
        self.cs.value(1)
        return self._BUFFER[0]

    def _read_u16(self, address):
        # Read a 16-bit BE unsigned value from the specified 8-bit address.
        self.cs.value(0)
        # buffer = bytearray(3)
        self._BUFFER[0] = address & 0x7F

        self._device.write(self._BUFFER[0:1])
        self._device.readinto(self._BUFFER)
        self.cs.value(1)
        return (self._BUFFER[0] << 8) | self._BUFFER[1]

    def _write_u8(self, address, val):
        # Write an 8-bit unsigned value to the specified 8-bit address.
        self.cs.value(0)
        address_byte = address | 0x80
        # buffer = bytearray(2)
        self._BUFFER[0] = address_byte
        self._BUFFER[1] = val
        self._device.write(self._BUFFER)
        self.cs.value(1)

    @property
    def bias(self):
        """The state of the sensor's bias (True/False)."""
        return bool(self._read_u8(_MAX31865_CONFIG_REG) & _MAX31865_CONFIG_BIAS)

    @bias.setter
    def bias(self, val):
        config = self._read_u8(_MAX31865_CONFIG_REG)
        if val:
            config |= _MAX31865_CONFIG_BIAS  # Enable bias.
        else:
            config &= ~_MAX31865_CONFIG_BIAS  # Disable bias.
        self._write_u8(_MAX31865_CONFIG_REG, config)

    @property
    def auto_convert(self):
        """The state of the sensor's automatic conversion
        mode (True/False).
        """
        return bool(self._read_u8(_MAX31865_CONFIG_REG) & _MAX31865_CONFIG_MODEAUTO)

    @auto_convert.setter
    def auto_convert(self, val):
        config = self._read_u8(_MAX31865_CONFIG_REG)
        if val:
            config |= _MAX31865_CONFIG_MODEAUTO  # Enable auto convert.
            config |= _MAX31865_CONFIG_BIAS  # Enable bias.
        else:
            config &= ~_MAX31865_CONFIG_MODEAUTO  # Disable auto convert.
            config &= ~_MAX31865_CONFIG_BIAS  # Disable bias.
        self._write_u8(_MAX31865_CONFIG_REG, config)

    @property
    def fault(self):
        """The fault state of the sensor.  Use :meth:`clear_faults` to clear the
        fault state.  Returns a 6-tuple of boolean values which indicate if any
        faults are present:

        - HIGHTHRESH
        - LOWTHRESH
        - REFINLOW
        - REFINHIGH
        - RTDINLOW
        - OVUV
        """
        faults = self._read_u8(_MAX31865_FAULTSTAT_REG)
        highthresh = bool(faults & _MAX31865_FAULT_HIGHTHRESH)
        lowthresh = bool(faults & _MAX31865_FAULT_LOWTHRESH)
        refinlow = bool(faults & _MAX31865_FAULT_REFINLOW)
        refinhigh = bool(faults & _MAX31865_FAULT_REFINHIGH)
        rtdinlow = bool(faults & _MAX31865_FAULT_RTDINLOW)
        ovuv = bool(faults & _MAX31865_FAULT_OVUV)
        return (highthresh, lowthresh, refinlow, refinhigh, rtdinlow, ovuv)

    def clear_faults(self):
        """Clear any fault state previously detected by the sensor."""
        config = self._read_u8(_MAX31865_CONFIG_REG)
        config &= ~0x2C
        config |= _MAX31865_CONFIG_FAULTSTAT
        self._write_u8(_MAX31865_CONFIG_REG, config)

    def read_rtd(self):
        """Perform a raw reading of the thermocouple and return its 15-bit
        value.  You'll need to manually convert this to temperature using the
        nominal value of the resistance-to-digital conversion and some math.  If you just want
        temperature use the temperature property instead.
        """
        if self.auto_convert:
            rtd = self._read_u16(_MAX31865_RTDMSB_REG)
            if not rtd & 1:
                rtd >>= 1
                return rtd
        self.clear_faults()
        self.bias = True
        time.sleep(0.01)
        config = self._read_u8(_MAX31865_CONFIG_REG)
        config |= _MAX31865_CONFIG_1SHOT
        self._write_u8(_MAX31865_CONFIG_REG, config)
        time.sleep(0.065)
        rtd = self._read_u16(_MAX31865_RTDMSB_REG)
        self.bias = False
        # Remove fault bit.
        rtd >>= 1
        return rtd

    @property
    def resistance(self):
        """Read the resistance of the RTD and return its value in Ohms."""
        resistance = self.read_rtd()
        resistance /= 32768
        resistance *= self.ref_resistor
        return resistance

    @property
    def temperature(self):
        """Read the temperature of the sensor and return its value in degrees
        Celsius.
        """
        # This math originates from:
        # http://www.analog.com/media/en/technical-documentation/application-notes/AN709_0.pdf
        # To match the naming from the app note we tell lint to ignore the Z1-4
        # naming.
        # pylint: disable=invalid-name
        raw_reading = self.resistance
        Z1 = -_RTD_A
        Z2 = _RTD_A * _RTD_A - (4 * _RTD_B)
        Z3 = (4 * _RTD_B) / self.rtd_nominal
        Z4 = 2 * _RTD_B
        temp = Z2 + (Z3 * raw_reading)
        temp = (math.sqrt(temp) + Z1) / Z4
        if temp >= 0:
            return temp

        # For the following math to work, nominal RTD resistance must be normalized to 100 ohms
        raw_reading /= self.rtd_nominal
        raw_reading *= 100

        rpoly = raw_reading
        temp = -242.02
        temp += 2.2228 * rpoly
        rpoly *= raw_reading  # square
        temp += 2.5859e-3 * rpoly
        rpoly *= raw_reading  # ^3
        temp -= 4.8260e-6 * rpoly
        rpoly *= raw_reading  # ^4
        temp -= 2.8183e-8 * rpoly
        rpoly *= raw_reading  # ^5
        temp += 1.5243e-10 * rpoly
        return temp