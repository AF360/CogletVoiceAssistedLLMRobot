# 1. System aktualisieren & PCIe Gen 3 aktivieren (wichtig für Performance!)
sudo apt update && sudo apt full-upgrade -y
sudo raspi-config
# -> Advanced Options -> PCIe Speed -> Gen 3
# -> Neustart

# 2. Hailo-Software installieren
sudo apt install hailo-all
# Das installiert Treiber, Firmware und die "hailo-tappas-core" Umgebung

# Ordner für Modelle anlegen
mkdir -p models
# YOLOv8n (große Hailo-8 Version) herunterladen
wget https://hailo-model-zoo.s3.eu-west-2.amazonaws.com/ModelZoo/Compiled/v2.11.0/hailo8l/yolov8n.hef -O models/yolov8n.hef

# im .venv 
pip install opencv-python

# HAILO8-Beispiel-Repo clonen
cd /opt/coglet-pi/hailo8
git clone https://github.com/hailo-ai/hailo-rpi5-examples.git
cd hailo-rpi5-examples
./install.sh
./download_resources.sh

# Testerkennung:
python basic_pipelines/detection.py --input /dev/video0
# falls hierbei wieder gemeckert wird wg. Hailo8L vs Hailo8, beim Aufruf das große HEF angeben: 
# --hef /opt/coglet-pi/hailo8/models/yolov8n.hef

# (je 10 bis max. 20 Bilder, hauptsächlich Tageslicht, 1-2 Kunstlicht, von vorn und schräg vorn)
mkdir -p /opt/coglet-pi/hailo8/dataset
mkdir -p /opt/coglet-pi/hailo8/dataset/barbara
mkdir -p /opt/coglet-pi/hailo8/dataset/andreas

# Ordner für Model-files
cd /opt/coglet-pi/hailo8/models

# 1. Face Detection (SCRFD 10g für Hailo-8)
wget https://hailo-model-zoo.s3.eu-west-2.amazonaws.com/ModelZoo/Compiled/v2.11.0/hailo8/scrfd_10g.hef

# 2. Face Recognition (ArcFace MobileFaceNet für Hailo-8)
wget https://hailo-model-zoo.s3.eu-west-2.amazonaws.com/ModelZoo/Compiled/v2.11.0/hailo8/arcface_mobilefacenet.hef

