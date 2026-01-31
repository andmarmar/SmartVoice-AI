import sys
import json
import time
import csv
import math
import threading
import queue
import os
import audioop
import numpy as np
from datetime import datetime

from vosk import Model, KaldiRecognizer, SpkModel
import pyaudio
from deep_translator import GoogleTranslator
from gtts import gTTS

from sense_hat import SenseHat
import cv2
from picamera2 import Picamera2
from PIL import ImageFont, ImageDraw, Image

import paho.mqtt.client as mqtt

# CONFIGURACIÓN GLOBAL

# MQTT
MQTT_BROKER = "broker.hivemq.com"
MQTT_PORT = 1883
MQTT_TOPIC = "proyecto_raspberri_andyjos/voz/frecuencias"

# Audio
INDICE_MICROFONO = 1  # Ajustar según 'arecord -l'
INPUT_RATE = 44100    # Entrada del micrófono
VOSK_RATE = 16000     # Requisito de Vosk
CHUNK_SIZE = 4096

# Visual
SENSIBILIDAD = 1500   # Para el vúmetro del SenseHAT
OFF = [0, 0, 0]
VERDE = [0, 255, 0]
AMARILLO = [255, 255, 0]
ROJO = [255, 0, 0]

PALETA_COLORES = [
    (0, 255, 255),      # Cyan
    (255, 0, 255),      # Magenta
    (255, 165, 0),      # Naranja
    (0, 255, 0),        # Verde Lima
    (100, 149, 237)     # Azul
]
COLOR_NEUTRO = (255, 255, 255)


# Colas para comunicación entre hilos
cola_audio_raw = queue.Queue()    # Del Micro -> Al Procesador IA
cola_tts = queue.Queue()          # De la IA -> Al Traductor/Altavoz
cola_datos = queue.Queue()        # De la IA -> Al Analista (MQTT/CSV)


class EstadoCompartido:
    def __init__(self):
        self.subtitulo = "Inicializando..."
        self.color_actual = COLOR_NEUTRO
        self.nombre_hablante = ""
        self.lock = threading.Lock()

    def actualizar(self, texto, nombre, color):
        with self.lock:
            self.subtitulo = texto
            self.nombre_hablante = nombre
            self.color_actual = color

    def leer(self):
        with self.lock:
            return self.subtitulo, self.nombre_hablante, self.color_actual

estado_sistema = EstadoCompartido()
evento_parada = threading.Event()

# INICIALIZACION

print(">>> Inicializando SenseHAT...")
sense = SenseHat()
sense.clear()
sense.low_light = True

print(">>> Cargando Modelos VOSK (Esto puede tardar)...")
try:
    model = Model("model-es")
    spk_model = SpkModel("model-spk")
except Exception as e:
    print(f"[ERROR CRÍTICO] No se encontraron los modelos: {e}")
    sys.exit(1)

# Variables de identificación de hablantes
known_speakers = []
speaker_names = []


def get_distance(vec1, vec2):
    dot_product = sum(a*b for a, b in zip(vec1, vec2))
    norm_a = math.sqrt(sum(a*a for a in vec1))
    norm_b = math.sqrt(sum(b*b for b in vec2))
    return 1 - (dot_product / (norm_a * norm_b))

def identificar_hablante(vector_voz):
    umbral = 0.85
    best_dist = 100
    speaker_idx = -1

    for i, known_vec in enumerate(known_speakers):
        dist = get_distance(vector_voz, known_vec)
        if dist < best_dist:
            best_dist = dist
            speaker_idx = i

    if best_dist < umbral and speaker_idx != -1:
        return speaker_names[speaker_idx], speaker_idx
    else:
        new_name = f"Hablante {len(known_speakers) + 1}"
        known_speakers.append(vector_voz)
        speaker_names.append(new_name)
        return new_name, len(speaker_names) - 1

def dibujar_onda_sensehat(fragmento_audio):
    try:
        rms = audioop.rms(fragmento_audio, 2)
        nivel = min(8, int((rms / SENSIBILIDAD) * 8))
        pixels = [OFF] * 64
        for fila in range(8):
            fila_real = 7 - fila
            if fila < nivel:
                color = VERDE
                if fila >= 4: color = AMARILLO
                if fila >= 6: color = ROJO
                for col in range(8):
                    pixels[fila_real * 8 + col] = color
        sense.set_pixels(pixels)
    except:
        pass

def poner_texto_pil(imagen_cv2, texto, color_texto):
    img_pil = Image.fromarray(cv2.cvtColor(imagen_cv2, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(img_pil)
    ancho_img, alto_img = img_pil.size
    
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 30)
    except:
        font = ImageFont.load_default()

    palabras = texto.split()
    lineas = []
    linea_actual = ""
    margen = 20
    
    for p in palabras:
        prueba = f"{linea_actual} {p}".strip()
        try:
            ancho = font.getlength(prueba)
        except:
            ancho = font.getsize(prueba)[0]
            
        if ancho <= (ancho_img - margen*2):
            linea_actual = prueba
        else:
            lineas.append(linea_actual)
            linea_actual = p
    if linea_actual: lineas.append(linea_actual)

    alto_linea = 35
    y_inicio = alto_img - 40 - (len(lineas) * alto_linea)
    
    for i, linea in enumerate(lineas):
        try: ancho_linea = font.getlength(linea)
        except: ancho_linea = font.getsize(linea)[0]
        
        x = (ancho_img - ancho_linea) // 2
        y = y_inicio + (i * alto_linea)
        
        for dx, dy in [(-2,-2), (-2,2), (2,-2), (2,2)]:
            draw.text((x+dx, y+dy), linea, font=font, fill=(0,0,0))
        draw.text((x, y), linea, font=font, fill=color_texto)

    return cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)


# HILO 1: CAPTURA DE AUDIO
def hilo_captura_audio():
    p = pyaudio.PyAudio()
    local_rate = INPUT_RATE
    try:
        stream = p.open(format=pyaudio.paInt16, channels=1, rate=local_rate, 
                        input=True, input_device_index=INDICE_MICROFONO, 
                        frames_per_buffer=CHUNK_SIZE)
    except:
        print("[Audio] Fallo 44.1kHz, probando 48kHz...")
        local_rate = 48000
        stream = p.open(format=pyaudio.paInt16, channels=1, rate=local_rate, 
                        input=True, input_device_index=INDICE_MICROFONO, 
                        frames_per_buffer=CHUNK_SIZE)
    
    print(f"[Hilo Audio] Escuchando a {local_rate}Hz")
    
    while not evento_parada.is_set():
        try:
            data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
            dibujar_onda_sensehat(data)
            cola_audio_raw.put((data, local_rate))
        except Exception as e:
            print(f"[Error Mic] {e}")
            break

    stream.stop_stream()
    stream.close()
    p.terminate()

# HILO 2: PROCESAMIENTO IA (VOSK)-
def hilo_procesamiento_ia():
    rec = KaldiRecognizer(model, VOSK_RATE, spk_model)
    print("[Hilo IA] Motor VOSK listo.")
    
    while not evento_parada.is_set():
        try:
            data_raw, sample_rate = cola_audio_raw.get(timeout=1)
            
            # Resamplear si es necesario (De 44.1/48k a 16k)
            if sample_rate != VOSK_RATE:
                data_vosk, _ = audioop.ratecv(data_raw, 2, 1, sample_rate, VOSK_RATE, None)
            else:
                data_vosk = data_raw

            if rec.AcceptWaveform(data_vosk):
                res = json.loads(rec.Result())
                texto = res.get("text", "").strip()
                spk_vec = res.get("spk")
                
                if texto:
                    # Identificar Hablante
                    nombre = "Desconocido"
                    color = COLOR_NEUTRO
                    if spk_vec:
                        nombre, idx = identificar_hablante(spk_vec)
                        color = PALETA_COLORES[idx % len(PALETA_COLORES)]
                    
                    print(f"[{nombre}]: {texto}")
                    
                    estado_sistema.actualizar(f"{nombre}: {texto}", nombre, color)
                    
                    cola_tts.put((texto, nombre))
                    
                    cola_datos.put(texto)
                    
            else:
                # Subtitulo en tiempo real
                parcial = json.loads(rec.PartialResult())
                texto_parcial = parcial.get("partial", "").strip()
                if texto_parcial:
                    _, nombre_last, color_last = estado_sistema.leer()
                    estado_sistema.actualizar(texto_parcial + "...", nombre_last, color_last)
                    
        except queue.Empty:
            continue
        except Exception as e:
            print(f"[Error IA] {e}")

# HILO 3: TRADUCCIÓN Y SÍNTESIS (TTS)
def hilo_traductor_tts():
    translator = GoogleTranslator(source='es', target='en')
    print("[Hilo TTS] Servicio de traducción listo.")
    
    while not evento_parada.is_set():
        try:
            texto_es, nombre = cola_tts.get(timeout=1)
            
            # 1. Traducir
            texto_en = translator.translate(texto_es)
            print(f"    >>> [EN]: {texto_en}")
            
            # 2. Sintetizar
            tts = gTTS(text=texto_en, lang='en', slow=False)
            filename = f"temp_{threading.get_ident()}_{int(time.time())}.mp3"
            tts.save(filename)
            
            # 3. Reproducir 
            os.system(f"mpg123 -q {filename}")
            
            # 4. Limpieza
            if os.path.exists(filename):
                os.remove(filename)
                
            cola_tts.task_done()
            
        except queue.Empty:
            continue
        except Exception as e:
            print(f"[Error TTS] {e}")

# HILO 4: DATA LOGGING Y MQTT
def hilo_datos_mqtt():
    client = mqtt.Client()
    connected = False
    try:
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.loop_start()
        connected = True
        print("[Hilo MQTT] Conectado.")
    except:
        print("[Hilo MQTT] Error de conexión. Trabajando offline.")

    frecuencias = {}
    ultimo_reporte = time.time()
    
    while not evento_parada.is_set():
        try:
            try:
                texto = cola_datos.get(timeout=1)
                palabras = texto.lower().split()
                for p in palabras:
                    p = p.strip(".,;?!")
                    if p: frecuencias[p] = frecuencias.get(p, 0) + 1
            except queue.Empty:
                pass
            
            ahora = time.time()
            if ahora - ultimo_reporte >= 180:
                if frecuencias:
                    # 1. Generar CSV, opcional
                    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                    # csv_name = f"log_{ts}.csv"
                    # with open(csv_name, "w", newline="") as f:
                    #     writer = csv.writer(f)
                    #     writer.writerow(["palabra", "frecuencia"])
                    #     for k, v in frecuencias.items():
                    #         writer.writerow([k, v])
                    
                    # 2. Enviar MQTT
                    if connected:
                        payload = {
                            "dispositivo": "RPi_SmartVoice",
                            "timestamp": ts,
                            "datos": sorted(frecuencias.items(), key=lambda x: -x[1])
                        }
                        client.publish(MQTT_TOPIC, json.dumps(payload))
                        print(f"[Data] Reporte enviado ({len(frecuencias)} palabras)")
                    
                    frecuencias.clear()
                ultimo_reporte = ahora
                
        except Exception as e:
            print(f"[Error Datos] {e}")

# MAIN

if __name__ == "__main__":
    picam2 = Picamera2()
    config = picam2.create_video_configuration(main={"size": (640, 480), "format": "RGB888"})
    picam2.configure(config)
    picam2.start()
    
    cv2.namedWindow("SmartVoice AR", cv2.WINDOW_NORMAL)
    estado_sistema.actualizar("Iniciando sistemas...", "", COLOR_NEUTRO)

    # Arranca Hilos
    hilos = [
        threading.Thread(target=hilo_captura_audio, daemon=True),
        threading.Thread(target=hilo_procesamiento_ia, daemon=True),
        threading.Thread(target=hilo_traductor_tts, daemon=True),
        threading.Thread(target=hilo_datos_mqtt, daemon=True)
    ]
    
    for t in hilos:
        t.start()
        
    print("\n>>> SISTEMA FUNCIONANDO EN PARALELO. Presiona 'q' para salir.\n")

    # Bucle Principal
    try:
        while True:
            # Captura frame
            frame = picam2.capture_array()
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            
            # Leer estado actual
            txt, _, color = estado_sistema.leer()
            
            # Dibujar AR
            frame = poner_texto_pil(frame, txt, color)
            
            # Mostrar
            cv2.imshow("SmartVoice AR", frame)
            
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
                
    except KeyboardInterrupt:
        print("\nDeteniendo...")
        
    finally:
        evento_parada.set()
        estado_sistema.actualizar("Apagando...", "", COLOR_NEUTRO)
        cv2.destroyAllWindows()
        picam2.stop()
        sense.clear()
        print("Sistema detenido")