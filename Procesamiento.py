import paho.mqtt.client as mqtt
import json
import csv
import math
import os
from datetime import datetime
from collections import Counter

# Configuración
BROKER = "broker.hivemq.com"
TOPIC = "proyecto_raspberri_andyjos/voz/frecuencias" 
ARCHIVO_METRICAS = "analisis_riqueza_lexica.csv"

# Calculo de métricas 

def hypergeometric_distribution_diversity(palabras):
    N = len(palabras)
    freq = Counter(palabras)
    hdd = 0
    sample_size = min(42, N)
    if sample_size == 0: return 0
    
    for f in freq.values():
        if f > 0:
            try:
                hdd += 1 - math.comb(N-f, sample_size) / math.comb(N, sample_size)
            except ValueError:
                pass # Protección por si los números son incompatibles
    return hdd / sample_size

def simpson_index(palabras):
    freq = Counter(palabras)
    N = len(palabras)
    if N == 0: return 0
    return 1 - sum((v/N)**2 for v in freq.values())

def shannon_entropy(palabras):
    freq = Counter(palabras)
    N = len(palabras)
    if N == 0: return 0
    return -sum((v/N) * math.log2(v/N) for v in freq.values())

def calcular_metrics(palabras):
    total = len(palabras)
    tipos = len(set(palabras))
    freq = Counter(palabras)
    
    hapax = sum(1 for v in freq.values() if v == 1)
    dis = sum(1 for v in freq.values() if v == 2)
    ttr = tipos / total if total > 0 else 0
    
    hdd = hypergeometric_distribution_diversity(palabras)
    simpson = simpson_index(palabras)
    entropy = shannon_entropy(palabras)
    
    return {
        'Total palabras': total,
        'Tipos únicos': tipos,
        'TTR': round(ttr, 4),
        'Hapax': hapax,
        'Dis Legomena': dis,
        'HDD': round(hdd, 4),
        'Simpson': round(simpson, 4),
        'Shannon Entropy': round(entropy, 4)
    }

 # Gestión de datos

def reconstruir_lista_palabras(datos_frecuencia):
    """
    Convierte [['hola', 3], ['mundo', 1]] 
    en ['hola', 'hola', 'hola', 'mundo']
    para que las fórmulas matemáticas funcionen.
    """
    lista_completa = []
    for palabra, cantidad in datos_frecuencia:
        # Añadimos la palabra tantas veces como se dijo
        lista_completa.extend([palabra] * int(cantidad))
    return lista_completa

def guardar_metricas_csv(timestamp, resultados):
    existe = os.path.isfile(ARCHIVO_METRICAS)
    
    with open(ARCHIVO_METRICAS, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        
        # Si es archivo nuevo, escribimos cabeceras
        if not existe:
            headers = ['Timestamp'] + list(resultados.keys())
            writer.writerow(headers)
        
        # Escribimos los datos
        fila = [timestamp] + list(resultados.values())
        writer.writerow(fila)
        
    print(f"   [Guardado] Métricas añadidas a {ARCHIVO_METRICAS}")

# Comunicación MQTT

def on_connect(client, userdata, flags, rc):
    print(f"--- CONECTADO Y ANALIZANDO ---")
    print(f"Escuchando en: {TOPIC}")
    print(f"Guardando resultados en: {ARCHIVO_METRICAS}")
    client.subscribe(TOPIC)

def on_message(client, userdata, msg):
    try:
        # 1. Recibir datos
        payload = json.loads(msg.payload.decode())
        timestamp = payload.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        datos_raw = payload.get("datos", []) # Lista de [palabra, frecuencia]
        
        print("\n" + "="*50)
        print(f"RECIBIDO - Hora: {timestamp}")
        
        if not datos_raw:
            print("   (El paquete está vacío, no se detectaron palabras)")
            return

        # 2. Transformar datos para el análisis
        palabras_expandidas = reconstruir_lista_palabras(datos_raw)
        
        # 3. Calcular métricas matemáticas
        metricas = calcular_metrics(palabras_expandidas)
        
        # 4. Mostrar en pantalla
        print("RESULTADOS DEL ANÁLISIS:")
        print(f"   - Total Palabras: {metricas['Total palabras']}")
        print(f"   - Riqueza (TTR):  {metricas['TTR']}")
        print(f"   - Entropía:       {metricas['Shannon Entropy']}")
        print(f"   - Complejidad (HDD): {metricas['HDD']}")

        # 5. Guardar en el CSV Maestro
        guardar_metricas_csv(timestamp, metricas)
        print("="*50)

    except Exception as e:
        print(f"Error procesando datos: {e}")

if __name__ == '__main__':
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(BROKER, 1883, 60)
        client.loop_forever()
    except KeyboardInterrupt:
        print("\nDeteniendo análisis...")