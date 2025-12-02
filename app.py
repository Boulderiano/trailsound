import streamlit as st
import gpxpy
from midiutil.MidiFile import MIDIFile
import math
import io
import os
import subprocess
import tempfile
import numpy as np

# --- ⚙️ AJUSTES DE MAPEO Y CONSTANTES MUSICALES ---

# RANGOS MIDI PARA TONO
RANGO_NOTAS_MELODIA = 48 # Rango de 4 octavas
ESCALA_BASE_MELODIA = 60 # Comienza en C4 (60)
MIN_PITCH_BAJO = 36      # C2
MAX_PITCH_BAJO = 48      # C3 (Rango de 1 octava)

# TRACKS Y CANALES MIDI
TRACK_MELODIA = 0
TRACK_BAJOS = 1
TRACK_PERCUSION = 2
CANAL_MELODIA = 0
CANAL_BAJOS = 0
CANAL_PERCUSION = 9      # Canal de percusión MIDI estándar (Drum Channel)

# AJUSTES DE ESCALA Y RITMO
PENTATONIC_SCALE = [0, 2, 4, 7, 9] # Escala Pentatónica Mayor (C, D, E, G, A)
TEMPO_BASE_BPM = 120 # Un poco más rápido para darle energía
DURACION_MINIMA_NOTA = 0.25 # Corchea (Valor MIDI base)

# AJUSTES DE DATOS GPX
MIN_CADENCE = 60.0
MAX_CADENCE = 200.0
VELOCIDAD_MIN_PARA_ESCALA = 0.5 # 1.8 km/h (Mínimo para escalado de Ritmo)
VELOCIDAD_MAX_PARA_ESCALA = 7.0 # 25.2 km/h (Máximo para escalado de Ritmo)
THRESHOLD_FAST_SPEED = 2.5 # m/s para activar el Snare (aprox. 9 km/h)

# AJUSTES ESPECÍFICOS DE RITMO/PERCUSIÓN
BOMBO_MIDI_NOTE = 36 # Kick Drum
CAJA_MIDI_NOTE = 38  # Snare Drum
PERCUSION_VELOCITY = 100
EMA_ALPHA = 0.10 # <-- Suavizado de la cadencia: Aumentado para mayor reactividad

# --- 🎶 FUNCIONES AUXILIARES DE AUDIO ---

def download_soundfont():
    """Descarga el SoundFont si no está presente en el entorno de Streamlit."""
    # Usar un sf3 más pequeño y común si el original causa problemas
    sf2_path = "FluidR3Mono_GM.sf3" 
    if not os.path.exists(sf2_path):
        st.info("Descargando el sintetizador de sonido (sólo la primera vez)...")
        # Asegúrate de que el contenedor de Streamlit tenga 'wget' y 'fluidsynth' instalados
        # En un entorno local, esto se ejecuta como un comando de shell.
        try:
            os.system(f"wget -q https://github.com/musescore/MuseScore/raw/master/share/sound/FluidR3Mono_GM.sf3")
        except:
            st.error("Error al descargar SoundFont. Verifica el entorno.")
            return None
    return sf2_path

@st.cache_data(show_spinner=False)
def convert_midi_to_audio(midi_buffer):
    """Convierte un buffer MIDI a un archivo WAV usando fluidsynth y devuelve el contenido."""
    sf2_path = download_soundfont()
    if sf2_path is None: return None
    midi_buffer.seek(0)
    
    with tempfile.NamedTemporaryFile(suffix='.mid', delete=False) as tmp_midi:
        tmp_midi.write(midi_buffer.read())
        tmp_midi_path = tmp_midi.name

    tmp_wav_path = tmp_midi_path.replace('.mid', '.wav')
    
    try:
        command = [
            "fluidsynth", "-ni", sf2_path, tmp_midi_path, "-F", tmp_wav_path, "-r", "44100"
        ]
        # Usar subprocess.DEVNULL para evitar output excesivo en la consola
        subprocess.run(command, check=True, capture_output=True) 
        
        with open(tmp_wav_path, "rb") as f:
            wav_content = f.read()
            
        return wav_content

    except subprocess.CalledProcessError as e:
        st.error(f"Error en la conversión de audio (Fluidsynth). Asegúrate de que 'fluidsynth' esté instalado. {e.stderr.decode()}")
        return None
    except FileNotFoundError:
        st.error("Fluidsynth no encontrado. Asegúrate de que esté instalado en tu sistema.")
        return None
    finally:
        if os.path.exists(tmp_midi_path):
             os.remove(tmp_midi_path)
        if os.path.exists(tmp_wav_path):
            os.remove(tmp_wav_path)

# --- 🎹 FUNCIONES AUXILIARES DE MAPEO Y LÓGICA ---

def snap_to_scale(pitch):
    """Ajusta un tono MIDI a la nota más cercana en la escala pentatónica."""
    pitch = int(round(pitch))
    octave = pitch // 12
    note_in_octave = pitch % 12
    min_diff = 12
    closest_note = 0
    
    for scale_note in PENTATONIC_SCALE:
        diff = abs(note_in_octave - scale_note)
        if diff < min_diff:
            min_diff = diff
            closest_note = scale_note
            
    return octave * 12 + closest_note

def get_cadence_from_point(p_curr):
    """Intenta leer la cadencia de la extensión del punto GPX."""
    try:
        # Verifica la estructura de la extensión de Cadence
        # Esto es compatible con la estructura más común de Garmin/Strava
        if hasattr(p_curr, 'extensions') and p_curr.extensions:
            for ext in p_curr.extensions:
                if hasattr(ext, 'cadence') and ext.cadence is not None:
                     return float(ext.cadence)
    except Exception:
        pass
    return None

def get_mapping_values(point, avg_speed, data_min_max):
    """Calcula los valores escalados (0.0 a 1.0) de Altitud, Ritmo y Cadencia."""
    
    ele_min, ele_max, ele_range = data_min_max['ele']
    
    # 1. Altitud (Escalado de 0.0 a 1.0)
    altitud_value = (point.elevation - ele_min) / ele_range if ele_range > 0 else 0.5
    altitud_scaled = max(0.0, min(1.0, altitud_value))
    
    # 2. Ritmo (Velocidad Absoluta, luego Escalado de 0.0 a 1.0)
    speed_scaled = (avg_speed - VELOCIDAD_MIN_PARA_ESCALA) / (VELOCIDAD_MAX_PARA_ESCALA - VELOCIDAD_MIN_PARA_ESCALA)
    speed_scaled = max(0.0, min(1.0, speed_scaled))

    # 3. Cadencia (Valor de pasos/min, luego Escalado de 0.0 a 1.0)
    cadence_value = get_cadence_from_point(point)
    if cadence_value is None:
        # Si no hay cadencia, se interpola con un valor base + velocidad escalada
        cadence_value = MIN_CADENCE + (speed_scaled * (MAX_CADENCE - MIN_CADENCE))
        
    cadence_scaled = (cadence_value - MIN_CADENCE) / (MAX_CADENCE - MIN_CADENCE)
    cadence_scaled = max(0.0, min(1.0, cadence_scaled))

    return {
        'Altitud': altitud_scaled,     # 0.0 (Min) a 1.0 (Max)
        'Ritmo (Velocidad)': speed_scaled, # 0.0 (Lento) a 1.0 (Rápido)
        'Cadencia': cadence_scaled    # 0.0 (Lento) a 1.0 (Rápido)
    }


@st.cache_data
def generate_midi_file(gpx_data_content, distance_step_m, tempo, melody_source, beat_source, bass_source):
    """Procesa los datos GPX y genera el archivo MIDI basado en los mapeos."""
    
    # Inicialización de estado interno
    smoothed_cadence = MIN_CADENCE
    
    try:
        # Manejo de codificación de archivos
        try:
            gpx_content = io.StringIO(gpx_data_content.decode('utf-8'))
        except UnicodeDecodeError:
            gpx_content = io.StringIO(gpx_data_content.decode('latin-1'))
            
        gpx = gpxpy.parse(gpx_content)
        
        if not gpx.tracks or not gpx.tracks[0].segments:
             raise ValueError("El archivo GPX no contiene tracks o segmentos válidos.")

        # 1. PRE-CÁLCULO (Rango de Altitud)
        segment = gpx.tracks[0].segments[0]
        all_elevations = [p.elevation for p in segment.points if p.elevation is not None]
        
        if not all_elevations:
            raise ValueError("El archivo GPX no contiene datos de elevación válidos.")

        ele_min = min(all_elevations)
        ele_max = max(all_elevations)
        ele_range = ele_max - ele_min

        data_min_max = {
            'ele': (ele_min, ele_max, ele_range)
        }
        
        # 2. INICIALIZACIÓN MIDI
        midifile = MIDIFile(3) # 3 pistas (Melodía, Bajos, Percusión)
        for track in range(3):
            midifile.addTempo(track, 0, tempo)
            
        # Asignación de instrumentos (General MIDI)
        midifile.addProgramChange(TRACK_MELODIA, CANAL_MELODIA, 0, 0)   # Piano
        midifile.addProgramChange(TRACK_BAJOS, CANAL_BAJOS, 0, 33)      # Bajo Eléctrico (Bass)
        # La pista de percusión no necesita Program Change

        
        pitch_base = ESCALA_BASE_MELODIA - RANGO_NOTAS_MELODIA / 2
        
        current_distance = 0.0
        next_note_distance = 0.0
        last_point_time = None
        time = 0.0
        
        # 3. GENERACIÓN DE NOTAS
        for i in range(len(segment.points)):
            p_curr = segment.points[i]

            if p_curr.elevation is None or p_curr.time is None: continue
                
            if i > 0:
                p_prev = segment.points[i-1]
                if p_prev.time is None: continue
                    
                distance_increment = p_curr.distance_3d(p_prev)
                distance_increment = distance_increment if distance_increment is not None else 0
                current_distance += distance_increment
                
                # Procesa un punto solo cuando se ha avanzado la distancia mínima (DISTANCE_STEP_M)
                if current_distance >= next_note_distance:
                    
                    # Cálculo de la velocidad promedio en este paso
                    if last_point_time is None:
                        avg_speed = 1.67 # Velocidad inicial promedio (5 km/h)
                    else:
                        delta_time_segment = (p_curr.time - last_point_time).total_seconds()
                        # Usar distance_step_m en el numerador para una velocidad más precisa para el BEAT
                        if delta_time_segment > 0:
                             avg_speed = distance_step_m / delta_time_segment 
                        else:
                             avg_speed = VELOCIDAD_MAX_PARA_ESCALA # Si el tiempo es 0, usar el máximo
                    
                    scaled_values = get_mapping_values(p_curr, avg_speed, data_min_max)
                    
                    # --- MAPEO A PARÁMETROS MUSICALES ---

                    # 1. MELODÍA (TONO)
                    melody_scaled_value = scaled_values[melody_source]
                    pitch_raw = pitch_base + (melody_scaled_value * RANGO_NOTAS_MELODIA)
                    pitch_melodia = snap_to_scale(pitch_raw)
                    pitch_melodia = int(pitch_melodia)
                    
                    # 2. DURACIÓN (BEAT/RITMO): Cuantizado para mejor musicalidad
                    beat_scaled_value = scaled_values[beat_source]
                    
                    if beat_scaled_value > 0.75:
                        duration = 0.25     # Corchea
                    elif beat_scaled_value > 0.5:
                        duration = 0.5      # Negra
                    elif beat_scaled_value > 0.25:
                        duration = 1.0      # Blanca
                    else:
                        duration = 2.0      # Redonda (más lento)

                    # 3. BAJOS (TONO)
                    bass_scaled_value = scaled_values[bass_source]
                    pitch_bajo_range = MAX_PITCH_BAJO - MIN_PITCH_BAJO
                    
                    # Se usa round() para asegurar que la nota sea un número entero y estable
                    pitch_bajo = MIN_PITCH_BAJO + round(bass_scaled_value * pitch_bajo_range)
                    pitch_bajo = int(pitch_bajo)
                    
                    # --- PERCUSIÓN (GENERACIÓN DE PULSOS) ---
                    
                    raw_cadence = scaled_values['Cadencia'] * (MAX_CADENCE - MIN_CADENCE) + MIN_CADENCE
                    
                    # Suavizado de la cadencia
                    if next_note_distance == 0.0:
                        smoothed_cadence = raw_cadence
                    else:
                        smoothed_cadence = EMA_ALPHA * raw_cadence + (1.0 - EMA_ALPHA) * smoothed_cadence
                        
                    # La cadencia (pasos/min) se convierte a pulsos (beats/min). 
                    # Generalmente son 2 pulsos (izq/der) por cadencia.
                    target_pulses_per_minute = smoothed_cadence * 2.0
                    
                    if target_pulses_per_minute > 0:
                        # beat_duration_midi es la duración de una corchea/negra
                        beat_duration_midi = (60.0 / target_pulses_per_minute) * (tempo / 60.0)
                    else:
                        beat_duration_midi = 1.0

                    # Añadir un máximo de pulsos de percusión dentro de la duración de la nota actual
                    num_pulses = math.floor(duration / beat_duration_midi)
                    
                    for j in range(num_pulses):
                        pulse_time = time + (j * beat_duration_midi)
                        
                        # Patrón simple Kick/Snare (asumiendo 4/4)
                        if j % 4 == 0:
                            # Bombo (Kick) en los tiempos 1 y 3 (o pulsos de cadencia)
                            midifile.addNote(TRACK_PERCUSION, CANAL_PERCUSION, BOMBO_MIDI_NOTE, pulse_time, 0.1, PERCUSION_VELOCITY)
                        elif j % 2 == 1:
                            # Caja (Snare) en los tiempos 2 y 4 (solo si el ritmo es rápido/intenso)
                            if scaled_values['Ritmo (Velocidad)'] > 0.5 and avg_speed >= THRESHOLD_FAST_SPEED:
                                midifile.addNote(TRACK_PERCUSION, CANAL_PERCUSION, CAJA_MIDI_NOTE, pulse_time, 0.1, PERCUSION_VELOCITY)
                        # También se puede añadir un Hi-Hat en todos los pulsos (opcional)
                        
                    # --- AÑADIR NOTAS A TRACKS PRINCIPALES ---
                    
                    # Ajustar la duración de la nota para que no sea muy corta (estacato)
                    note_duration = max(0.25, duration * 0.9) 
                    
                    midifile.addNote(TRACK_MELODIA, CANAL_MELODIA, pitch_melodia, time, note_duration, 100)
                    midifile.addNote(TRACK_BAJOS, CANAL_BAJOS, pitch_bajo, time, note_duration, 90)
                    
                    # Actualizar para la siguiente iteración
                    next_note_distance += distance_step_m
                    last_point_time = p_curr.time
                    time += duration
            
            # Limitar la duración máxima del MIDI por si el archivo GPX es muy largo
            if time >= 600: break 

        midi_buffer = io.BytesIO()
        midifile.writeFile(midi_buffer)
        midi_buffer.seek(0)
        return midi_buffer
        
    except ValueError as e:
        raise e
    except Exception as e:
        st.exception(e)
        raise ValueError("Error al procesar el archivo GPX.")


# --- 🖥️ FUNCIÓN PRINCIPAL DE STREAMLIT ---

def main():
    st.set_page_config(
        page_title="Trail Sonification App", 
        layout="centered",
        # 🔑 ESTA ES LA CLAVE: Fuerza la barra lateral a estar siempre expandida
        initial_sidebar_state="expanded" 
    )

 # 1. CSS para estilizar el contenedor (simplificado)
    hide_streamlit_style = """
        <style>
        /* 1. OCULTAR ELEMENTOS NATIVOS */
        #MainMenu, footer, header {visibility: hidden;} 
        .stApp header button[title="Collapse sidebar"] {
            display: none !important;
        }

        /* 2. FORZAR COLORES OSCUROS Y TEXTO BLANCO */
        .stApp { 
            background-color: #0a0708 !important; /* Fondo principal negro */
        }
        
        /* 💡 NUEVA REGLA CRÍTICA: Fuerza el fondo de la barra lateral a negro/gris oscuro */
        .stSidebar {
            background-color: #3B3B3B !important; 
        }

        /* Fuerza el texto de la barra lateral y principal a blanco */
        .stSidebar * {
            color: white !important; 
        }

        /* Asegura que títulos principales y botones sigan siendo blancos */
        h1, h2, h3, p, .stDownloadButton label { 
            color: white !important; 
        }

        /* Otros ajustes */
        .block-container { padding-top: 2rem !important; }
        </style>
    """
    st.markdown(hide_streamlit_style, unsafe_allow_html=True)
    
    st.markdown(
        "<h1 style='text-align: center; font-size: 4em; font-weight: 900; color: white !important; margin-bottom: 0;'> <i> TRAIL SOUND </i></h1>",
        unsafe_allow_html=True
    )
    st.markdown(
        "<p style='text-align: center; font-size: 1.5em; color: #aaa !important;'>Transforma tus carreras en melodías únicas</p>",
        unsafe_allow_html=True
    )


    # --- Sidebar Configuration (Controls) ---
    st.sidebar.header("⚙️ Ajustes de Mapeo")

    # Slider para la densidad de notas (Sustituye a la duración total)
    resolution = st.sidebar.slider(
        "**1. Resolución Espacial (m/nota)**", 
        min_value=5, max_value=50, value=15, step=5,
        help="Distancia recorrida por cada nota generada (menor valor = más notas y una canción más larga)."
    )
    tempo = st.sidebar.slider(
        "**2. Tempo Base (BPM)**",
        min_value=60, max_value=180, value=120, step=10,
        help="Define la velocidad general de la música."
    )
    
    st.sidebar.markdown("---")
    st.sidebar.markdown("<h3 style='text-align: center;'>Selector de Datos (0.0 a 1.0)</h3>", unsafe_allow_html=True)
    
    SOURCES = ['Altitud', 'Ritmo (Velocidad)', 'Cadencia']

    # Selectores de Mapeo
    melody_source = st.sidebar.selectbox(
        "**Melodía (Tono)**",
        SOURCES,
        index=0, # Por defecto: Altitud
        help="El dato que controlará el tono (grave/agudo) de la melodía."
    )
    beat_source = st.sidebar.selectbox(
        "**Ritmo Armónico (Duración)**",
        SOURCES,
        index=1, # Por defecto: Ritmo (Velocidad)
        help="El dato que controlará la duración de las notas (lento/rápido) de la melodía y el bajo."
    )
    bass_source = st.sidebar.selectbox(
        "**Bajos (Tono)**",
        SOURCES,
        index=2, # Por defecto: Cadencia
        help="El dato que controlará el tono del bajo (grave/agudo)."
    )
    st.sidebar.markdown("---") 

    # --- Uploader (Centrado) ---
    with st.container(border=True):
        st.markdown(
            "<h2 style='text-align: center; font-size: 1.5em; color: #333 !important;'>Sube tu archivo GPX:</h2>",
            unsafe_allow_html=True
        )
        uploaded_file = st.file_uploader(
            "Archivo GPX",
            type=["gpx"],
            label_visibility="collapsed",
            help="Click aquí para subir o arrastrar tu archivo GPX."
        )

    # --- Procesamiento y Descarga ---
    if uploaded_file is not None:
        gpx_data_content = uploaded_file.read()
        
        with st.spinner('Procesando datos y componiendo tu Trail Sound...'):
            try:
                midi_buffer = generate_midi_file(
                    gpx_data_content,
                    resolution, # distance_step_m
                    tempo,
                    melody_source,
                    beat_source,
                    bass_source
                )
                
                st.success("¡Composición finalizada! Archivo MIDI generado.")
                
                # --- REPRODUCTOR DE AUDIO ---
                wav_content = convert_midi_to_audio(midi_buffer)
                
                if wav_content:
                    st.subheader("Escucha tu Melodía:")
                    st.audio(wav_content, format='audio/wav')
                else:
                    st.warning("No se pudo generar el reproductor de audio, pero puedes descargar el archivo MIDI.")

                # 2. Botón de Descarga
                st.download_button(
                    label="Descargar Archivo MIDI (.mid)",
                    data=midi_buffer,
                    file_name=f"trail_music_{melody_source}_{beat_source}.mid",
                    mime="audio/midi",
                    help="Haz clic para descargar tu canción."
                )
                st.info("Abre el archivo MIDI con cualquier software de música para escucharlo con un instrumento diferente.")
                
            except ValueError as e:
                st.error(f"Error en los datos: {e}")
            except Exception as e:
                st.error("Ocurrió un error inesperado. Revisa tu archivo GPX y los parámetros.")
                st.exception(e)

if __name__ == "__main__":
    main()
