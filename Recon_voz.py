import sys
import json
import time
import csv
import math
from datetime import datetime
import audioop
from vosk import Model, KaldiRecognizer, SpkModel # Importamos SpkModel
import pyaudio
import os
import numpy as np


import threading
from deep_translator import GoogleTranslator
from gtts import gTTS

from sense_hat import SenseHat

import cv2
from picamera2 import Picamera2
from PIL import ImageFont, ImageDraw, Image

import paho.mqtt.client as mqtt #pip install paho-mqtt

# Configuracion MQTT

MQTT_BROKER = "broker.hivemq.com"  
MQTT_PORT = 1883
MQTT_TOPIC = "proyecto_raspberri_andyjos/voz/frecuencias"

client_mqtt = mqtt.Client()

# Configuracion
INDICE_MICROFONO = 1    #Importante abir el alsamixer para activar el micro y subir volumen de los cascos
INPUT_RATE = 44100   # Tasa que le gusta al USB (44.1kHz)
VOSK_RATE = 16000    # Tasa que necesita Vosk (16kHz)

sense = SenseHat()
sense.clear()
sense.low_light = True

OFF = [0, 0, 0]
VERDE = [0, 255, 0]
AMARILLO = [255, 255, 0]
ROJO = [255, 0, 0]

SENSIBILIDAD = 1500


PALETA_COLORES = [
    (0, 255, 255),
    (255, 0, 255),  # Magenta (Hablante 2)
    (255, 165, 0),  # Naranja (Hablante 3)
    (0, 255, 0),    # Verde Lima (Hablante 4)
    (100, 149, 237) # Azul (Hablante 5)
]
COLOR_NEUTRO = (255, 255, 255)

TEXTO_PARA_MOSTRAR = "Esperando voz..." 
NOMBRE_HABLANTE_ACTUAL = ""

print("Cargando modelos (Voz y Hablantes)...")

picam2 = Picamera2()
config = picam2.create_video_configuration(
    main={"size": (640, 480), "format": "RGB888"}
)
picam2.configure(config)
picam2.start()

cv2.namedWindow("Subtitulos en Vivo", cv2.WINDOW_NORMAL)

try:
    model = Model("model-es") 
    spk_model = SpkModel("model-spk")
except Exception as e:
    print(f"Error cargando modelos: {e}")
    sys.exit()

rec = KaldiRecognizer(model, VOSK_RATE, spk_model)

p = pyaudio.PyAudio()

print(f"Abriendo microfono ID {INDICE_MICROFONO} a {INPUT_RATE}Hz...")

try:
    stream = p.open(format=pyaudio.paInt16, 
                    channels=1, 
                    rate=INPUT_RATE, 
                    input=True, 
                    input_device_index=INDICE_MICROFONO,
                    frames_per_buffer=4096)
except Exception as e:
    print(f"\n[ERROR] Fallo al abrir a {INPUT_RATE}Hz. Intentando con 48000Hz...")
    try:
        # Si 44100 falla, probamos 48000 (el otro estandar)
        INPUT_RATE = 48000
        stream = p.open(format=pyaudio.paInt16, 
                        channels=1, 
                        rate=INPUT_RATE, 
                        input=True, 
                        input_device_index=INDICE_MICROFONO,
                        frames_per_buffer=4096)
    except Exception as e2:
        print(f"\n[ERROR FATAL] El microfono no acepta ni 44.1k ni 48k.")
        print(f"Error: {e2}")
        sys.exit()

stream.start_stream()


subtitulo_actual = "Escuchando..."
texto_final_confirmado = ""
momento_ultima_palabra = time.time()

known_speakers = [] 
speaker_names = []

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("[MQTT] Conectado al broker exitosamente.")
    else:
        print(f"[MQTT] Fallo al conectar. Codigo: {rc}")

client_mqtt.on_connect = on_connect

# Intentamos conectar (en un try por si el broker no esta listo)
try:
    client_mqtt.connect(MQTT_BROKER, MQTT_PORT, 60)
    client_mqtt.loop_start() # Inicia el hilo en segundo plano para manejar la red
except Exception as e:
    print(f"[MQTT Error] No se pudo conectar al inicio: {e}")


def dibujar_onda(fragmento_audio):
    """Calcula el volumen y dibuja barras en el Sense HAT"""
    try:
        # Calcular RMS (Root Mean Square) -> Volumen promedio del fragmento
        rms = audioop.rms(fragmento_audio, 2)
        
        # Convertir el volumen (0 a SENSIBILIDAD) a un numero de filas (0 a 8)
        nivel = min(8, int((rms / SENSIBILIDAD) * 8))
        
        # Crear la matriz de pixeles (64 pixeles)
        pixels = [OFF] * 64
        
        for fila in range(8):
            # Invertimos la fila para que el 0 sea abajo (SenseHAT tiene 0,0 arriba)
            fila_real = 7 - fila
            
            if fila < nivel:
                # Elegir color
                color = VERDE
                if fila >= 4: color = AMARILLO
                if fila >= 6: color = ROJO
                
                for col in range(8):
                    pixels[fila_real * 8 + col] = color
        
        sense.set_pixels(pixels)
        
    except Exception:
        pass

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

    if speaker_idx != -1:
        print(f"   (Diferencia con {speaker_names[speaker_idx]}: {best_dist:.4f})")
    
    if best_dist < umbral and speaker_idx != -1:
        return speaker_names[speaker_idx]
    else:
        new_name = f"Hablante {len(known_speakers) + 1}"
        known_speakers.append(vector_voz)
        speaker_names.append(new_name)
        return new_name

frecuencia_palabras = {}
tiempo_inicio = time.time()


def actualizar_frecuencias(texto):
    palabras = texto.lower().split()
    for p in palabras:
        p = p.strip(".,;:Â¿?Â¡!()\"'")
        if p:
            frecuencia_palabras[p] = frecuencia_palabras.get(p, 0) + 1

# Función usada para generar el CSV en raspberri, opcional
def generar_csv():
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    nombre_archivo = f"palabras_frecuencia_{timestamp}.csv"

    with open(nombre_archivo, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["palabra", "frecuencia"])
        for palabra, freq in sorted(frecuencia_palabras.items(), key=lambda x: -x[1]):
            writer.writerow([palabra, freq])

    print(f"\n[CSV generado] {nombre_archivo}\n")
    

def enviar_reporte_frecuencias():

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")    
    datos_ordenados = sorted(frecuencia_palabras.items(), key=lambda x: -x[1])
    
    # Diccionario (JSON object) para enviar
    payload = {
        "dispositivo": "RaspberryPi_Vosk",
        "timestamp": timestamp,
        "datos": datos_ordenados 
    }
    mensaje_json = json.dumps(payload)

    # MQTT
    try:
        info = client_mqtt.publish(MQTT_TOPIC, mensaje_json)
        info.wait_for_publish() # Esperar confirmacion de envio
        print(f"\n[MQTT] Datos enviados a '{MQTT_TOPIC}'")
    except Exception as e:
        print(f"\n[MQTT Error] Fallo al publicar: {e}")


def narrar_traduccion(texto_original, hablante):
    try:
        traductor = GoogleTranslator(source='es', target='en')
        texto_ingles = traductor.translate(texto_original)
        
        print(f"   >>> [Traduccion]: {texto_ingles}")
        
        tts = gTTS(text=texto_ingles, lang='en', slow=False)
        
        nombre_mp3 = f"temp_{int(time.time()*1000)}.mp3"
        tts.save(nombre_mp3)
        
        os.system(f"mpg123 -q {nombre_mp3}")
        
        os.remove(nombre_mp3)

    except Exception as e:
        print(f"[Error Traductor]: {e}")
        

def poner_texto_con_tildes(imagen_cv2, texto, color_texto):
    img_pil = Image.fromarray(cv2.cvtColor(imagen_cv2, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(img_pil)
    ancho_img, alto_img = img_pil.size
    try:
        font_size = 30
        # Ruta estandar en Raspberry Pi
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
    except IOError:
        # Si no la encuentra, carga la por defecto
        font = ImageFont.load_default()
        
        
    margen_lateral = 20
    ancho_maximo = ancho_img - (margen_lateral * 2)
    
    palabras = texto.split()
    lineas = []
    linea_actual = ""
    
    for palabra in palabras:
        prueba = linea_actual + " " + palabra if linea_actual else palabra
        try:
            ancho_prueba = font.getlength(prueba)
        except AttributeError:
            ancho_prueba = font.getsize(prueba)[0]
        
        if ancho_prueba <= ancho_maximo:
            linea_actual = prueba
        else:
            lineas.append(linea_actual)
            linea_actual = palabra
            
    if linea_actual:
        lineas.append(linea_actual)
        
    alto_linea = font_size + 5
    y_inicio = alto_img - 30 - (len(lineas) * alto_linea)
    
    for i, linea in enumerate(lineas):
        try:
            ancho_linea = font.getlength(linea)
        except:
            ancho_linea = font.getsize(linea)[0]
            
        x = (ancho_img - ancho_linea) // 2 
        y = y_inicio + (i * alto_linea)
        
        rango = 2
        for dx in range(-rango, rango + 1):
            for dy in range(-rango, rango + 1):
                draw.text((x + dx, y + dy), linea, font=font, fill=(0, 0, 0))
                
        draw.text((x, y), linea, font=font, fill=color_texto)
             
    return cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)

print("Habla cuando quieras (Detectando hablantes...):\n")

color_actual_pantalla = COLOR_NEUTRO

while True:
    try:
        # 1. Leer datos raw del microfono
        data = stream.read(4096, exception_on_overflow=False)
        
        dibujar_onda(data)
        
        # 2. CONVERSION IMPORTANTE: De 44100Hz a 16000Hz
        data, _ = audioop.ratecv(data, 2, 1, INPUT_RATE, VOSK_RATE, None)

        if rec.AcceptWaveform(data):
            resultado = json.loads(rec.Result())
            texto = resultado.get("text", "").strip()

            spk_vector = resultado.get("spk")

            if texto:
                nombre_hablante = "Desconocido"
                indice_color = -1

                if spk_vector:
                    nombre_hablante = identificar_hablante(spk_vector)
                    if nombre_hablante in speaker_names:
                        idx = speaker_names.index(nombre_hablante)
                        color_actual_pantalla = PALETA_COLORES[idx % len(PALETA_COLORES)]
                    else:
                        color_actual_pantalla = COLOR_NEUTRO

                print(f"\n[{nombre_hablante}]: {texto}")
                
                texto_final_confirmado = f"{nombre_hablante}: {texto}"
                subtitulo_actual = ""
            
                actualizar_frecuencias(texto)
                t = threading.Thread(target=narrar_traduccion, args=(texto, nombre_hablante))
                t.start()
                subtitulo_actual = ""      
                print("")

        else:
            parcial = json.loads(rec.PartialResult())
            texto = parcial.get("partial", "").strip()

            if texto:
                subtitulo_actual = texto
                color_actual_pantalla = COLOR_NEUTRO
                sys.stdout.write("\r" + "..." + subtitulo_actual + " " * 20)
                sys.stdout.flush()
                momento_ultima_palabra = time.time()
                
        #Video
        frame = picam2.capture_array()
        
        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        
        if subtitulo_actual:
            texto_pantalla = subtitulo_actual
            color_a_usar = COLOR_NEUTRO
        else:
            texto_pantalla = texto_final_confirmado
            color_a_usar = color_actual_pantalla
            
           
            
        alto, ancho, _ = frame.shape
        frame = poner_texto_con_tildes(frame, texto_pantalla, color_a_usar)        
        cv2.imshow("Subtitulos en Vivo", frame)
        
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
        

        if time.time() - tiempo_inicio >= 180:
            enviar_reporte_frecuencias()
            #generar_csv()
            frecuencia_palabras.clear()
            tiempo_inicio = time.time()

    except KeyboardInterrupt:
            print("\nSaliendo y apagando luces...")
            sense.clear() # Apagar luces al salir
            break
            
stream.stop_stream()
stream.close()
p.terminate()
picam2.stop()
cv2.destroyAllWindows()
