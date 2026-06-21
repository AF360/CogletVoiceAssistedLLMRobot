import sys
import struct
import usb.core
import usb.util
import time


PARAMETERS = {
    "VERSION": (48, 0, 3, "ro", "uint8"),
    "AEC_AZIMUTH_VALUES": (33, 75, 16, "ro", "radians"),
    "DOA_VALUE": (20, 18, 4, "ro", "uint16"),
    "REBOOT": (48, 7, 1, "wo", "uint8"),
}

class ReSpeaker:
    TIMEOUT = 100000

    def __init__(self, dev):
        self.dev = dev

    def write(self, name, data_list):
        try:
            data = PARAMETERS[name]
        except KeyError:
            return

        if data[3] == "ro":
            raise ValueError('{} is read-only'.format(name))

        if len(data_list) != data[2]:
            raise ValueError('{} value count is not {}'.format(name, data[2]))

        windex = data[0]
        wvalue = data[1]
        data_type = data[4]
        data_cnt = data[2]
        payload = []

        if data_type == 'float' or data_type == 'radians':
            for i in range(data_cnt):
                payload += struct.pack(b'f', float(data_list[i]))
        elif data_type == 'char' or data_type == 'uint8':
            for i in range(data_cnt):
                payload += data_list[i].to_bytes(1, byteorder='little')
        else:
            for i in range(data_cnt):
                payload += struct.pack(b'i', data_list[i])

        print("WriteCMD: cmdid: {}, resid: {}, payload: {}".format(wvalue, windex, payload))

        self.dev.ctrl_transfer(
            usb.util.CTRL_OUT | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_DEVICE,
            0, wvalue, windex, payload, self.TIMEOUT)


    def read(self, name):
        try:
            data = PARAMETERS[name]
        except KeyError:
            return

        resid = data[0]
        cmdid = 0x80 | data[1]
        length = data[2] + 1

        response = self.dev.ctrl_transfer(
            usb.util.CTRL_IN | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_DEVICE,
            0, cmdid, resid, length, self.TIMEOUT)

        if data[4] == 'uint8':
            result = response.tolist()
        elif data[4] == 'radians':
            byte_data = response.tobytes()
            num_values = ( length - 1 ) / 4
            match_str = '<'
            for i in range(int(num_values)):
                match_str += 'f'
            result = struct.unpack(match_str, byte_data[1:length])
        elif data[4] == 'uint16':

            byte_data = response.tobytes()

            num_values = data[2] // 2
            match_str = '<' + 'H' * num_values

            result = struct.unpack(match_str, byte_data[1:1+data[2]])

        return result

    def close(self):
        """
        close the interface
        """
        usb.util.dispose_resources(self.dev)


def find(vid=0x2886, pid=0x0018):

    dev = usb.core.find(idVendor=vid, idProduct=pid)
    if not dev:

        dev = usb.core.find(idVendor=vid)
        if dev:
            print(f"Gefundenes Gerät: VID 0x{dev.idVendor:04x} PID 0x{dev.idProduct:04x}")
        return dev

    return ReSpeaker(dev)

def main():
    dev = find()
    if not dev:
        print('No device found (VID 0x2886)')
        sys.exit(1)

    try:
        ver = dev.read("VERSION")
        print('{}: {}'.format("VERSION", list(ver) if ver else "Error"))
    except Exception as e:
        print(f"Warnung beim Lesen der Version: {e}")

    print("Drücke STRG+C zum Beenden.")

    try:
        while True:

            result = dev.read("DOA_VALUE")
            if result:


                print('{}: {}, {}: {}'.format("SPEECH_DETECTED", result[1], "DOA_VALUE", result[0]))
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nBeendet.")
    finally:
        dev.close()

if __name__ == '__main__':
    main()
