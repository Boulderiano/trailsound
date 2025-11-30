import streamlit as st
import gpxpy
from midiutil.MidiFile import MIDIFile
import math
import io
import os
import subprocess
import tempfile
from pydub import AudioSegment # Necesario para el manejo de audio

# --- AJUSTES DE MAPEO ---
ESCALA_BASE = 60
RANGO_NOTAS = 48
TEMPO_BASE_BPM = 100
DURACION_MINIMA_NOTA = 0.25
VELOCIDAD_MAX_PARA_DURACION = 5.0
PENTATONIC_SCALE = [0, 2, 4, 7, 9] 
MIN_CADENCE = 60.0
MAX_CADENCE = 200.0
MIN_PERCUSION_PITCH = 35 
MAX_PERCUSION_PITCH = 81
PERCUSION_VELOCITY = 100
TRACK_MELODIA = 0
TRACK_PERCUSION = 1           
CANAL_PERCUSION = 9           

# --- FUNCIONES AUXILIARES DE AUDIO ---

def download_soundfont():
    """Descarga el SoundFont si no está presente en el entorno de Streamlit."""
    sf2_path = "FluidR3Mono_GM.sf3"
    # Solo intenta descargar si el archivo no existe
    if not os.path.exists(sf2_path):
        st.info("Descargando el sintetizador de sonido (sólo la primera vez)...")
        # Usamos os.system para asegurar que wget se ejecute en el entorno
        os.system(f"wget -q https://github.com/musescore/MuseScore/raw/master/share/sound/FluidR3Mono_GM.sf3")
    return sf2_path

@st.cache_data(show_spinner=False)
def convert_midi_to_audio(midi_buffer):
    """Convierte un buffer MIDI a un archivo WAV usando fluidsynth y devuelve el contenido."""
    
    sf2_path = download_soundfont()
    midi_buffer.seek(0)
    
    # 1. Creamos un archivo temporal para el MIDI
    with tempfile.NamedTemporaryFile(suffix='.mid', delete=False) as tmp_midi:
        tmp_midi.write(midi_buffer.read())
        tmp_midi_path = tmp_midi.name

    # 2. Definimos la ruta de salida del WAV
    tmp_wav_path = tmp_midi_path.replace('.mid', '.wav')
    
    # 3. Ejecutamos fluidsynth para convertir MIDI a WAV
    try:
        command = [
            "fluidsynth", "-ni", sf2_path, tmp_midi_path, "-F", tmp_wav_path, "-r", "44100"
        ]
        subprocess.run(command, check=True, capture_output=True)
        
        # 4. Leemos el contenido WAV y limpiamos los archivos temporales
        with open(tmp_wav_path, "rb") as f:
            wav_content = f.read()
        
        return wav_content

    except subprocess.CalledProcessError as e:
        st.error(f"Error en la conversión de audio (Fluidsynth): {e.stderr.decode()}")
        return None
    finally:
        # 5. Limpieza
        os.remove(tmp_midi_path)
        if os.path.exists(tmp_wav_path):
            os.remove(tmp_wav_path)

# --- FUNCIONES AUXILIARES DE MAPEO ---

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
        if hasattr(p_curr, 'extensions') and p_curr.extensions and hasattr(p_curr.extensions[0], 'cadence') and p_curr.extensions[0].cadence is not None:
            return float(p_curr.extensions[0].cadence)
    except (AttributeError, IndexError, ValueError):
        pass
    return None

# --- FUNCIÓN DE LÓGICA CENTRAL (SONIFICACIÓN) ---
@st.cache_data
def generate_midi_file(gpx_data_content, scale_factor, tempo):
    """Procesa los datos GPX y devuelve el archivo MIDI en un buffer."""
    
    try:
        gpx_content = io.StringIO(gpx_data_content.decode('utf-8'))
    except UnicodeDecodeError:
        gpx_content = io.StringIO(gpx_data_content.decode('latin-1'))
        
    gpx = gpxpy.parse(gpx_content)

    all_elevations = []
    segment = gpx.tracks[0].segments[0]
    total_distance_m = 0.0
    
    for i in range(len(segment.points)):
         p_curr = segment.points[i]
         if p_curr.elevation is not None: all_elevations.append(p_curr.elevation)
         if i > 0:
            p_prev = segment.points[i-1]
            distance_val = p_curr.distance_3d(p_prev)
            total_distance_m += distance_val if distance_val is not None else 0
    
    if not all_elevations:
        raise ValueError("El archivo GPX no contiene datos de elevación válidos.")

    ele_min = min(all_elevations)
    ele_max = max(all_elevations)
    ele_range = ele_max - ele_min
    
    notes_needed = scale_factor * tempo
    DISTANCE_STEP_M = max(5.0, total_distance_m / notes_needed)
    
    midifile = MIDIFile(2)
    for track in range(2):
        midifile.addTempo(track, 0, tempo)
    
    midifile.addProgramChange(TRACK_MELODIA, 0, 0, 0)
    
    pitch_base = ESCALA_BASE - RANGO_NOTAS / 2
    
    current_distance = 0.0
    next_note_distance = 0.0
    last_point_time = None
    time = 0.0
    
    for i in range(len(segment.points)):
        p_curr = segment.points[i]

        if p_curr.elevation is None or p_curr.time is None: continue
            
        if i > 0:
            p_prev = segment.points[i-1]
            if p_prev.time is None: continue
                
            distance_increment = p_curr.distance_3d(p_prev)
            distance_increment = distance_increment if distance_increment is not None else 0
            current_distance += distance_increment
            
            if current_distance >= next_note_distance:
                
                if last_point_time is None:
                    avg_speed = 1.67
                else:
                    delta_time_segment = (p_curr.time - last_point_time).total_seconds()
                    avg_speed = DISTANCE_STEP_M / delta_time_segment if delta_time_segment > 0 else VELOCIDAD_MAX_PARA_DURACION 
                
                cadence = get_cadence_from_point(p_curr)
                if cadence is None:
                    cadence = 100 + (avg_speed * 20) 
                
                cadence = max(MIN_CADENCE, min(MAX_CADENCE, cadence))

                if ele_range > 0:
                    relative_position = (p_curr.elevation - ele_min) / ele_range
                    pitch_raw = pitch_base + (relative_position * RANGO_NOTAS)
                    pitch = snap_to_scale(pitch_raw) 
                else:
                    pitch = ESCALA_BASE
                
                pitch_melodia = int(pitch)
                
                speed_factor = max(0, 1 - (avg_speed / VELOCIDAD_MAX_PARA_DURACION))
                duration = DURACION_MINIMA_NOTA + (speed_factor * (4.0 - DURACION_MINIMA_NOTA))

                cadence_range = MAX_CADENCE - MIN_CADENCE
                percussion_pitch_range = MAX_PERCUSION_PITCH - MIN_PERCUSION_PITCH
                
                if cadence_range > 0:
                    cadence_position = (cadence - MIN_CADENCE) / cadence_range
                    percussion_pitch = MIN_PERCUSION_PITCH + round(cadence_position * percussion_pitch_range)
                else:
                    percussion_pitch = MIN_PERCUSION_PITCH
                
                percussion_pitch = int(percussion_pitch)
                
                midifile.addNote(TRACK_MELODIA, 0, pitch_melodia, time, duration, 100)
                midifile.addNote(TRACK_PERCUSION, CANAL_PERCUSION, percussion_pitch, time, duration, PERCUSION_VELOCITY)

                next_note_distance += DISTANCE_STEP_M
                last_point_time = p_curr.time
                time += duration
        
        if time >= 1000: break

    midi_buffer = io.BytesIO()
    midifile.writeFile(midi_buffer)
    midi_buffer.seek(0)
    return midi_buffer

# --- FUNCIÓN PRINCIPAL DE STREAMLIT ---
def main():
    st.set_page_config(page_title="Trail Sonification App", layout="centered")

    # 1. CSS para estilizar el contenedor y ocultar la UI de Streamlit
    hide_streamlit_style = """
        <style>
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        header {visibility: hidden;}
        
        /* Estilo para la tarjeta central */
        .stContainerStyle {
            background-color: white;
            padding: 30px;
            border-radius: 10px;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
            width: 100%;
            max-width: 500px;
            margin: 20px auto;
        }

        /* Oculta la etiqueta del uploader (solo muestra el recuadro de drag & drop) */
        .stFileUploader label > div {
            font-size: 1.2em !important;
            padding: 10px 0;
            text-align: center;
        }
        .stFileUploader > div > div > label > div > div:nth-child(2) {
            display: none;
        }
        </style>
    """
    st.markdown(hide_streamlit_style, unsafe_allow_html=True)

    # --- Títulos Centrales ---
    col1, col2, col3 = st.columns([1, 2, 1])
    
    with col2:
        st.write("") 
        st.markdown(
            "<h1 style='text-align: center; font-size: 4em; font-weight: 900; color: #333; margin-bottom: 0;'>TRAIL SOUND</h1>", 
            unsafe_allow_html=True
        )
        st.markdown(
            "<p style='text-align: center; font-size: 1.5em; color: #555;'>Transform your trail runs into unique melodies</p>", 
            unsafe_allow_html=True
        )
        st.markdown("---") 

    # --- Contenedor Central para la Carga y Ajustes ---
    col_card_left, col_card_center, col_card_right = st.columns([1, 3, 1])
    
    with col_card_center:
        # 1. Inicio del Contenedor Estilizado
        st.markdown(
            f'<div class="stContainerStyle">', 
            unsafe_allow_html=True
        )
        
        st.markdown(
            "<h2 style='text-align: center; font-size: 1.5em;'>Arraste o suba su archivo GPX:</h2>", 
            unsafe_allow_html=True
        )
        
        # --- CARGADOR DE ARCHIVOS (Elemento principal de la interacción) ---
        uploaded_file = st.file_uploader(
            "Archivo GPX", 
            type=["gpx"],
            label_visibility="collapsed", 
            help="Arrastre y suelte su archivo GPX aquí, o haga clic para seleccionar."
        )

        st.markdown("---")

        # --- SLIDERS DE AJUSTE ---
        target_minutes = st.slider(
            "**Duración Total de la Canción (min)**", 
            min_value=0.5, max_value=5.0, value=1.0, step=0.1,
            help="Establece la duración deseada para la pieza musical."
        )
        tempo = st.slider(
            "**Tempo Base (BPM)**", 
            min_value=60, max_value=180, value=100, step=10,
            help="Define la velocidad general de la música."
        )
        
        # 2. Cierre del contenedor estilizado
        st.markdown('</div>', unsafe_allow_html=True) 

    # --- Procesamiento y Descarga (Ocurre al subir el archivo) ---
    if uploaded_file is not None:
        gpx_data_content = uploaded_file.read()
        
        # Columna de centrado para el mensaje de éxito y el botón
        col_msg1, col_msg2, col_msg3 = st.columns([1, 3, 1])
        with col_msg2:
            with st.spinner('Procesando datos y componiendo...'):
                try:
                    # Usamos la escala de minutos del slider * 0.4 para la compresión (factor de escala)
                    scale_factor = target_minutes * 0.4 
                    midi_buffer = generate_midi_file(gpx_data_content, scale_factor, tempo)
                    
                    st.success("¡Composición finalizada! Tu archivo MIDI está listo.")
                    
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
                        file_name=f"trail_music_{target_minutes}min_{tempo}bpm.mid",
                        mime="audio/midi",
                        help="Haz clic para descargar tu canción."
                    )
                    st.info("Abre el archivo MIDI con cualquier reproductor musical o software de notación.")
                    
                except Exception as e:
                    st.error("Ocurrió un error. Asegúrate de que el GPX sea válido y tenga datos de elevación.")
                    st.exception(e)

if __name__ == "__main__":
    main()
