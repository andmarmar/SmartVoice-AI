# SmartVoice AI

SmartVoice AI is a real-time, augmented reality (AR) capable speech recognition and translation system designed for the Raspberry Pi. The project captures live audio and video, performs offline speech-to-text (in Spanish) and speaker identification, translates the speech into English, synthesizes it aloud, and provides real-time AR subtitling. Additionally, it features an analytical backend that calculates lexical richness and complexity metrics from the transcribed speech via MQTT.

## Features

* **Offline Speech Recognition & Speaker Identification**: Uses [Vosk](https://alphacephei.com/vosk/) models (`model-es` and `model-spk`) to locally process Spanish audio, recognize words, and identify different speakers without needing an internet connection.
* **Real-time Translation & TTS**: Translates recognized Spanish text to English using `deep_translator` and reads the English translation aloud using `gTTS`.
* **Augmented Reality (AR) Subtitles**: Overlays recognized text with speaker-specific color coding directly onto a live camera feed using OpenCV and Picamera2.
* **Visual Audio Feedback**: Utilizes the Raspberry Pi Sense HAT LED matrix as a VU meter to visualize sound intensity levels.
* **Lexical Complexity Analysis**: Computes advanced linguistic metrics like Shannon Entropy, Simpson Index, Hypergeometric Distribution Diversity (HDD), and Type-Token Ratio (TTR) based on word frequency.
* **IoT Data Transmission**: Inter-process communication using MQTT to log and analyze data asynchronously.

## Hardware Requirements

* Raspberry Pi (e.g., Raspberry Pi 4)
* Raspberry Pi Camera Module (Picamera2 compatible)
* USB Microphone
* Sense HAT
* Speaker / Audio Output (for TTS)

## Software Dependencies

Ensure your system is updated and install the required Python libraries. You may need to install some system packages (e.g., `mpg123` for TTS playback, `arecord` for audio).

```bash
pip install vosk pyaudio deep-translator gTTS sense-hat opencv-python Pillow paho-mqtt numpy
```

You must also download the required Vosk models and extract them into the same directory as the scripts:
* **Vosk Spanish Model**: Extract and rename the folder to `model-es`.
* **Vosk Speaker Model**: Extract and rename the folder to `model-spk`.

## System Architecture

The project is split into two main Python scripts working in tandem:

### 1. `Recon_voz.py` (Main System)
This script runs the core functionalities on the Raspberry Pi using multi-threading:
* **Audio Capture Thread**: Reads from the microphone and updates the Sense HAT display.
* **AI Processing Thread**: Uses Vosk for speech-to-text and matches vocal vectors to identify distinct speakers.
* **Translation & TTS Thread**: Takes transcription, translates it to English, and plays the audio via `mpg123`.
* **MQTT Data Logging Thread**: Periodically counts word frequencies and publishes them as an MQTT payload to a public HiveMQ broker.
* **Main Thread (Video & AR)**: Captures Picamera2 frames, adds subtitles using Pillow/OpenCV, and displays the UI.

### 2. `Procesamiento.py` (Analytics Backend)
This script acts as the data consumer:
* Subscribes to the specific MQTT topic (`proyecto_raspberri_andyjos/voz/frecuencias`).
* Reconstructs the spoken text based on received frequencies.
* Calculates complex linguistic metrics:
  * Total words & Unique types
  * Type-Token Ratio (TTR)
  * Hapax & Dis Legomena
  * Hypergeometric Distribution Diversity (HDD)
  * Simpson Index
  * Shannon Entropy
* Logs the calculated results locally into `analisis_riqueza_lexica.csv`.

## How to Run

1. **Start the Analytics Script**:
   It is recommended to start the processing script first so it can begin listening for incoming data.
   ```bash
   python3 Procesamiento.py
   ```

2. **Start the Main Application**:
   Ensure your camera, microphone, and Sense HAT are correctly connected. You may need to adjust `INDICE_MICROFONO` in the script depending on your ALSA audio device index (use `arecord -l` to find the correct index).
   ```bash
   python3 Recon_voz.py
   ```

3. **Stop the Application**:
   Press `q` on the active AR video window to gracefully terminate all threads and shut down the camera and UI.

## Lexical Metics Explained

The processing script calculates several statistical metrics to evaluate the richness of the spoken text:
* **TTR (Type-Token Ratio)**: The ratio of unique words to total words.
* **Hapax Legomena**: Words that only appear exactly once.
* **Shannon Entropy**: Measures the unpredictability or information content of the vocabulary used.
* **HDD (Hypergeometric Distribution Diversity)**: A robust measure of vocabulary diversity, less affected by sample size than simple TTR.
* **Simpson Index**: A measure of diversity indicating the probability that two randomly selected words are the same.

## Credits and Acknowledgements

Created by Andrés Martínez Márquez and José Antonio García Campanario.

## Contact

For questions, suggestions, or contributions, contact the repository maintainer or open an issue on GitHub.
