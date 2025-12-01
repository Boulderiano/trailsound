import streamlit as st
import gpxpy
from midiutil.MidiFile import MIDIFile
import math
import io
import os
import subprocess
import tempfile

# --- AJUSTES DE MAPEO ---
ESCALA_BASE = 60
RANGO_NOTAS = 48
TEMPO_BASE_BPM = 100
DURACION_MINIMA_NOTA = 0.125 
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

# --- AJUSTES ESPECÍFICOS DE RITMO/PERCUSIÓN ---
BOMBO_MIDI_NOTE = 36          
CAJA_MIDI_NOTE = 38           
THRESHOLD_FAST_SPEED = 3.0    
EMA_ALPHA = 0.05 # Factor de suavizado para la Cadencia (0.05 = transición muy suave)

# --- INICIALIZACIÓN GLOBAL ---
smoothed_cadence = MIN_CADENCE 


# --- FUNCIONES AUXILIARES DE AUDIO ---

def download_soundfont():
    """Descarga el SoundFont si no está presente en el entorno de Streamlit."""
    sf2_path = "FluidR3Mono_GM.sf3"
    if not os.path.exists(sf2_path):
        st.info("Downloading sound synthesizer (first time only)...")
        os.system(f"wget -q https://github.com/musescore/MuseScore/raw/master/share/sound/FluidR3Mono_GM.sf3")
    return sf2_path

@st.cache_data(show_spinner=False)
def convert_midi_to_audio(midi_buffer):
    """Convierte un buffer MIDI a un archivo WAV usando fluidsynth y devuelve el contenido."""
    
    sf2_path = download_soundfont()
    midi_buffer.seek(0)
    
    with tempfile.NamedTemporaryFile(suffix='.mid', delete=False) as tmp_midi:
        tmp_midi.write(midi_buffer.read())
        tmp_midi_path = tmp_midi.name

    tmp_wav_path = tmp_midi_path.replace('.mid', '.wav')
    
    try:
        command = [
            "fluidsynth", "-ni", sf2_path, tmp_midi_path, "-F", tmp_wav_path, "-r", "44100"
        ]
        subprocess.run(command, check=True, capture_output=True)
        
        with open(tmp_wav_path, "rb") as f:
            wav_content = f.read()
        
        return wav_content

    except subprocess.CalledProcessError as e:
        st.error(f"Error in audio conversion (Fluidsynth): {e.stderr.decode()}")
        return None
    finally:
        os.remove(tmp_midi_path)
        if os.path.exists(tmp_wav_path):
            os.remove(tmp_wav_path)

# --- FUNCIONES AUXILIARES DE MAPEO Y LÓGICA ---

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

def get_mapping_values(point, avg_speed, data_min_max):
    """Calcula los valores escalados de Altitud, Ritmo y Cadencia para un punto."""
    
    ele_min, ele_max, ele_range = data_min_max['ele']
    altitud_value = (point.elevation - ele_min) / ele_range if ele_range > 0 else 0.5
    
    ritmo_value = avg_speed
    
    cadence_value = get_cadence_from_point(point)
    if cadence_value is None:
        cadence_value = 100 + (avg_speed * 20) 
    
    cadence_scaled = (cadence_value - MIN_CADENCE) / (MAX_CADENCE - MIN_CADENCE)
    cadence_scaled = max(0.0, min(1.0, cadence_scaled))

    return {
        'Altitud': altitud_value,     # 0.0 (Min) a 1.0 (Max)
        'Ritmo (Velocidad)': ritmo_value, # Valor absoluto (m/s)
        'Cadencia': cadence_scaled    # 0.0 (Min) a 1.0 (Max)
    }


@st.cache_data
def generate_midi_file(gpx_data_content, scale_factor, tempo, melody_source, beat_source, bass_source):
    """Procesa los datos GPX usando las asignaciones de variables del usuario."""
    
    global smoothed_cadence 
    
    try:
        gpx_content = io.StringIO(gpx_data_content.decode('utf-8'))
    except UnicodeDecodeError:
        gpx_content = io.StringIO(gpx_data_content.decode('latin-1'))
        
    gpx = gpxpy.parse(gpx_content)

    # 1. PRE-CÁLCULO (Rango de Altitud y Distancia Total)
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
        raise ValueError("The GPX file contains no valid elevation data.")

    ele_min = min(all_elevations)
    ele_max = max(all_elevations)
    ele_range = ele_max - ele_min

    data_min_max = {
        'ele': (ele_min, ele_max, ele_range)
    }
    
    notes_needed = scale_factor * tempo
    DISTANCE_STEP_M = max(5.0, total_distance_m / notes_needed)
    
    midifile = MIDIFile(3) # 3 tracks (Melody, Percussion, Bass)
    for track in range(3):
        midifile.addTempo(track, 0, tempo)
    
    # Asignación de instrumentos
    midifile.addProgramChange(TRACK_MELODIA, 0, 0, 67)   # Saxofón Tenor (67)
    midifile.addProgramChange(2, 0, 0, 35)              # Fretless Bass (35)
    
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
                
                scaled_values = get_mapping_values(p_curr, avg_speed, data_min_max)
                
                # 1. MELODY (PITCH)
                melody_scaled_value = scaled_values[melody_source]
                pitch_raw = pitch_base + (melody_scaled_value * RANGO_NOTAS)
                pitch_melodia = snap_to_scale(pitch_raw) 
                pitch_melodia = int(pitch_melodia)
                
                # 2. DURATION (BEAT): 
                if beat_source == 'Ritmo (Velocidad)':
                    beat_speed = scaled_values['Ritmo (Velocidad)']
                else:
                    beat_value = 1.0 - scaled_values[beat_source]
                    beat_speed = beat_value * VELOCIDAD_MAX_PARA_DURACION
                
                speed_factor = max(0, 1 - (beat_speed / VELOCIDAD_MAX_PARA_DURACION))
                duration = DURACION_MINIMA_NOTA + (speed_factor * (4.0 - DURACION_MINIMA_NOTA))

                # 3. BASS (PITCH)
                bass_scaled_value = scaled_values[bass_source]
                MIN_PITCH_BAJO = 24 
                MAX_PITCH_BAJO = 48  
                pitch_bajo_range = MAX_PITCH_BAJO - MIN_PITCH_BAJO
                
                pitch_bajo = MIN_PITCH_BAJO + round(bass_scaled_value * pitch_bajo_range)
                pitch_bajo = int(pitch_bajo)
                
                # --- PERCUSSION (PULSE/CADENCE) ---
                
                raw_cadence = scaled_values['Cadencia'] * (MAX_CADENCE - MIN_CADENCE) + MIN_CADENCE
                
                if next_note_distance == 0.0:
                    smoothed_cadence = raw_cadence
                else:
                    smoothed_cadence = EMA_ALPHA * raw_cadence + (1.0 - EMA_ALPHA) * smoothed_cadence
                
                cadence_for_beat = smoothed_cadence
                
                target_pulses_per_minute = cadence_for_beat * 2.0 
                
                if target_pulses_per_minute > 0:
                    beat_duration_midi = tempo / target_pulses_per_minute
                else:
                    beat_duration_midi = 1.0

                # 🎯 CAMBIO CLAVE: Un solo golpe de tambor por nota melódica, el ritmo lo da la duración de la melodía.
                percussion_note = BOMBO_MIDI_NOTE 
                
                # Mapeamos la cadencia suavizada al volumen (Velocity) para la intensidad
                MIN_VEL = 60
                MAX_VEL = 127
                cadence_normalized = (smoothed_cadence - MIN_CADENCE) / (MAX_CADENCE - MIN_CADENCE)
                percussion_velocity = int(MIN_VEL + cadence_normalized * (MAX_VEL - MIN_VEL))
                
                
                # --- ADD NOTES TO TRACKS ---
                midifile.addNote(TRACK_MELODIA, 0, pitch_melodia, time, duration, 100)
                midifile.addNote(2, 0, pitch_bajo, time, duration, 90)                 

                # Percusión: Toca una vez, su volumen refleja la cadencia.
                midifile.addNote(TRACK_PERCUSION, CANAL_PERCUSION, percussion_note, time, 0.1, percussion_velocity)

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

    # 1. CSS for styling and hiding Streamlit UI
    hide_streamlit_style = """
        <style>
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        header {visibility: hidden;}
        
        /* Forces dark background */
        .stApp {
            background-color: #0E1117 !important; 
        }

        /* Style for the central card: WHITE background for interactive elements */
        .stContainerStyle {
            background-color: #0E1117;
            padding: 30px;
            border-radius: 10px;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5); 
            width: 100%;
            max-width: 500px;
            margin: 20px auto;
        }
        
        /* Central titles should be WHITE on the dark background */
        h1, h2, h3, p {
            color: white !important; 
        }

        /* Uploader title inside the white card should be dark */
        .stContainerStyle h2 {
            color: #333 !important;
        }

        /* Uploader styling */
        .stFileUploader label > div {
            font-size: 1.2em !important;
            padding: 10px 0;
            text-align: center;
        }
        .stFileUploader > div > div > label > div > div:nth-child(2) {
            display: none;
        }
        
        /* Adjust top margin for centering */
        .block-container {
            padding-top: 2rem !important; 
        }
        </style>
    """
    st.markdown(hide_streamlit_style, unsafe_allow_html=True)


    # --- Sidebar Configuration (New Location for Controls) ---
    st.sidebar.header("Musical Adjustments")

    # Sliders
    target_minutes = st.sidebar.slider(
        "**Song Duration (min)**", 
        min_value=0.5, max_value=5.0, value=1.0, step=0.1,
        help="Sets the desired duration for the piece."
    )
    tempo = st.sidebar.slider(
        "**Base Tempo (BPM)**", 
        min_value=60, max_value=180, value=100, step=10,
        help="Defines the general speed of the music."
    )
    
    st.sidebar.markdown("---")
    st.sidebar.markdown("<h3 style='text-align: center;'>Data Assignment</h3>", unsafe_allow_html=True)
    
    SOURCES = ['Altitud', 'Ritmo (Velocidad)', 'Cadencia']

    # Selectors
    melody_source = st.sidebar.selectbox(
        "**1. Melody (Pitch)**",
        SOURCES,
        index=0, 
        help="Data that controls the notes (low/high)."
    )
    beat_source = st.sidebar.selectbox(
        "**2. Beat (Duration/Pulse)**",
        SOURCES,
        index=1, 
        help="Data that controls the length of the notes (slow/fast)."
    )
    bass_source = st.sidebar.selectbox(
        "**3. Bass (Pitch)**",
        SOURCES,
        index=2, 
        help="Data that controls the bass tone (low/high)."
    )
    st.sidebar.markdown("---") # Sidebar separator

    # --- Central Titles and Uploader ---
    col1, col2, col3 = st.columns([1, 2, 1])
    
    with col2:
        st.write("") 
        st.markdown(
            "<h1 style='text-align: center; font-size: 4em; font-weight: 900; color: white !important; margin-bottom: 0;'>TRAIL SOUND</h1>", 
            unsafe_allow_html=True
        )
        st.markdown(
            "<p style='text-align: center; font-size: 1.5em; color: #aaa !important;'>Transform your trail runs into unique melodies</p>", 
            unsafe_allow_html=True
        )
        
        # --- Contenedor para la Carga (La única estructura visible en el centro) ---
        st.markdown(
            f'<div class="stContainerStyle">', 
            unsafe_allow_html=True
        )
        
        st.markdown(
            "<h2 style='text-align: center; font-size: 1.5em;'>Drag or upload your GPX file:</h2>", 
            unsafe_allow_html=True
        )
        
        uploaded_file = st.file_uploader(
            "GPX File", 
            type=["gpx"],
            label_visibility="collapsed", 
            help="Drag and drop your GPX file here, or click to select."
        )

        st.markdown('</div>', unsafe_allow_html=True) 

    # --- Procesamiento y Download ---
    if uploaded_file is not None:
        gpx_data_content = uploaded_file.read()
        
        col_msg1, col_msg2, col_msg3 = st.columns([1, 2, 1])
        with col_msg2:
            with st.spinner('Processing data and composing...'):
                try:
                    scale_factor = target_minutes * 0.4 
                    
                    midi_buffer = generate_midi_file(
                        gpx_data_content, 
                        scale_factor, 
                        tempo,
                        melody_source, 
                        beat_source, 
                        bass_source
                    )
                    
                    st.success("Composition complete! Your MIDI file is ready.")
                    
                    # --- AUDIO PLAYER ---
                    wav_content = convert_midi_to_audio(midi_buffer)
                    
                    if wav_content:
                        st.subheader("Listen to your Melody:")
                        st.audio(wav_content, format='audio/wav')
                    else:
                        st.warning("Could not generate the audio player, but you can download the MIDI file.")

                    # 2. Download Button
                    st.download_button(
                        label="Download MIDI File (.mid)",
                        data=midi_buffer,
                        file_name=f"trail_music_{melody_source}_{beat_source}.mid",
                        mime="audio/midi",
                        help="Click to download your song."
                    )
                    st.info("Open the MIDI file with any music player or notation software.")
                    
                except Exception as e:
                    st.error("An error occurred. Ensure the GPX file is valid and contains elevation data.")
                    st.exception(e)

if __name__ == "__main__":
    main()
