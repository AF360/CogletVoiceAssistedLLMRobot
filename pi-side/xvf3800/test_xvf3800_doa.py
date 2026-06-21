#!/usr/bin/env python3
import usb.core
import usb.util
import time
import sys


VID = 0x2886
PID = 0x0018

def find_device():

    dev = usb.core.find(idVendor=VID)
    if dev is None:
        raise ValueError("Kein ReSpeaker (VID 0x2886) gefunden!")
    print(f"Gefunden: ReSpeaker (VID: 0x{dev.idVendor:04x}, PID: 0x{dev.idProduct:04x})")
    return dev

def read_register(dev, reg_id):


    try:


        ret = dev.ctrl_transfer(0xC0, 0, 0, 0, 8, timeout=1000)

        return ret
    except Exception as e:
        print(f"Fehler beim Lesen: {e}")
        return None

def main():
    try:
        dev = find_device()
    except Exception as e:
        print(e)
        return

    print("Versuche DOA (Richtung) und VAD (Stimme) zu lesen...")
    print("Sprich jetzt laut und bewege dich um das Mikrofon herum!")


    try:


        ret = dev.ctrl_transfer(0xC0, 0, 0, 0, 4, timeout=500)
        print(f"Basis-Kommunikation OK: {list(ret)}")
    except Exception as e:
        print("\nSCHLECHTE NACHRICHT:")
        print(f"Das Gerät lehnt Control-Transfers ab: {e}")
        print("Das bestätigt vermutlich deine Vermutung: Die 'Lite' USB-Firmware")
        print("bietet (aktuell) keine Steuerschnittstelle, nur reines Audio.")
        return


if __name__ == "__main__":
    main()
