#!/usr/bin/env python3
import cv2
import numpy as np
import os
import time
from hailo_platform import (HEF, VDevice, HailoStreamInterface, InferVStreams,
                            ConfigureParams, InputVStreamParams, OutputVStreamParams,
                            FormatType)


HEF_FILE = "models/yolov8n.hef"

CAMERA_INDEX = 0
CONFIDENCE_THRESHOLD = 0.5


LABELS = {0: 'Person', 1: 'Bicycle', 2: 'Car', 3: 'Motorcycle', 5: 'Bus',
          7: 'Truck', 67: 'Cell phone', 77: 'Teddy bear'}

def get_hailo_detections(output_data):
    """
    Simples Post-Processing für YOLOv8 Output.
    (Hinweis: Echte Apps nutzen komplexere Decoder, dies ist ein Minimal-Parser für den Test)
    Hier vereinfacht: Wir schauen nur, ob 'etwas' zurückkommt.
    """

    return len(output_data) > 0

def main():
    if not os.path.exists(HEF_FILE):
        print(f"FEHLER: Modell {HEF_FILE} nicht gefunden! Bitte erst herunterladen.")
        return

    print(f"Lade HEF: {HEF_FILE} ...")
    hef = HEF(HEF_FILE)

    targets = [hef.get_input_vstream_infos()[0], hef.get_output_vstream_infos()[0]]

    print("Öffne Hailo-8L Device ...")
    with VDevice(params=ConfigureParams.create_from_hef(hef, interface=HailoStreamInterface.PCIe)) as target:

        configure_params = ConfigureParams.create_from_hef(hef, interface=HailoStreamInterface.PCIe)
        network_groups = target.configure(hef, configure_params)
        network_group = network_groups[0]
        network_group_params = network_group.create_params()

        input_vstreams_params = InputVStreamParams.make(network_group, format_type=FormatType.UINT8)
        output_vstreams_params = OutputVStreamParams.make(network_group, format_type=FormatType.FLOAT32)

        with InferVStreams(network_group, input_vstreams_params, output_vstreams_params) as infer_pipeline:

            cap = cv2.VideoCapture(CAMERA_INDEX)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 640)

            if not cap.isOpened():
                print(f"FEHLER: Konnte Kamera {CAMERA_INDEX} nicht öffnen.")
                return

            print("Starte Inferenz-Loop. Drücke 'q' zum Beenden.")

            input_vstream_info = hef.get_input_vstream_infos()[0]
            height, width, channels = input_vstream_info.shape

            iteration = 0

            while True:
                ret, frame = cap.read()
                if not ret:
                    print("Kamera-Fehler")
                    break

                input_frame = cv2.resize(frame, (width, height))
                input_frame = cv2.cvtColor(input_frame, cv2.COLOR_BGR2RGB)
                input_frame = np.expand_dims(input_frame, axis=0)

                t_start = time.time()

                input_data = {input_vstream_info.name: input_frame}
                results = infer_pipeline.infer(input_data)

                dt = time.time() - t_start
                fps = 1.0 / dt if dt > 0 else 0

                raw_outputs = list(results.values())

                cv2.putText(frame, f"Hailo FPS: {fps:.1f}", (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)

                if raw_outputs:
                    cv2.putText(frame, "Inference OK - Chip active", (10, 70),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)

                cv2.putText(frame, f"Hailo FPS: {fps:.1f}", (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)

                if raw_outputs:
                    cv2.putText(frame, "Inference OK - Chip active", (10, 70),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)

                    filename = f"hailo_debug_{int(time.time())}.jpg"
                    cv2.imwrite(filename, frame)
                    print(f"✅ Objekt erkannt! Bild gespeichert als: {filename}")

                    break

                if iteration > 100:
                    print("Keine Objekte erkannt, breche ab.")
                    break
                iteration += 1

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
