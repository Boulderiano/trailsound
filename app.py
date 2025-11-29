import streamlit as st
import gpxpy
from midiutil.MidiFile import MIDIFile
import math
import io

# --- AJUSTES DE MAPEO ---
ESCALA_BASE = 60
RANGO_NOTAS = 48
DURACION_MINIMA_NOTA = 0.25
VELOCIDAD_MAX_PARA_DURACION = 5.0 
PENTATONIC_SCALE = [0, 2, 4, 7, 9] 
MIN_CADENCE = 60.0
MAX_CADENCE = 200.0
MIN_PERCUSION_PITCH = 35 
MAX_PERCUSION_PITCH = 81

# 🎵 Tracks
TRACK_MELODIA = 0
TRACK_PERCUSION = 1           
CANAL_PERCUSION = 9           
PERCUSION_VELOCITY = 100

# --- FUNCIONES AUXILIARES ---

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
def generate_midi_file(gpx_data_content, target_minutes, tempo):
    """Procesa los datos GPX y devuelve el archivo MIDI en un buffer."""
    
    # Manejar la decodificación del archivo subido
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
            total_distance_m += p_curr.distance_3d(p_prev) if p_prev.distance_3d(p_curr) is not None else 0
    
    if not all_elevations:
        raise ValueError("El archivo GPX no contiene datos de elevación válidos.")

    ele_min = min(all_elevations)
    ele_max = max(all_elevations)
    ele_range = ele_max - ele_min
    
    # Cálculo Dinámico del Paso de Muestreo (Escalado de la Distancia)
    notes_needed = target_minutes * tempo
    DISTANCE_STEP_M = max(5.0, total_distance_m / notes_needed)
    
    # 2. Inicialización MIDI
    midifile = MIDIFile(2)
    for track in range(2):
        midifile.addTempo(track, 0, tempo)
    
    midifile.addProgramChange(TRACK_MELODIA, 0, 0, 0) # Piano
    
    pitch_base = ESCALA_BASE - RANGO_NOTAS / 2
    
    current_distance = 0.0
    next_note_distance = 0.0
    last_point_time = None
    time = 0.0
    
    # 3. Iteración y Muestreo
    for i in range(len(segment.points)):
        p_curr = segment.points[i]

        if p_curr.elevation is None or p_curr.time is None: continue
            
        if i > 0:
            p_prev = segment.points[i-1]
            if p_prev.time is None: continue
                
            distance_increment = p_curr.distance_3d(p_prev) if p_prev.distance_3d(p_curr) is not None else 0
            current_distance += distance_increment
            
            if current_distance >= next_note_distance:
                
                # CÁLCULO DE VELOCIDAD
                if last_point_time is None:
                    avg_speed = 1.67
                else:
                    delta_time_segment = (p_curr.time - last_point_time).total_seconds()
                    avg_speed = DISTANCE_STEP_M / delta_time_segment if delta_time_segment > 0 else VELOCIDAD_MAX_PARA_DURACION 
                
                # LECTURA DE CADENCIA
                cadence = get_cadence_from_point(p_curr)
                if cadence is None:
                    # Cadencia Estimada (Fallback)
                    cadence = 100 + (avg_speed * 20) 
                
                cadence = max(MIN_CADENCE, min(MAX_CADENCE, cadence))

                # 1. MELODÍA (ALTITUD)
                if ele_range > 0:
                    relative_position = (p_curr.elevation - ele_min) / ele_range
                    pitch_raw = pitch_base + (relative_position * RANGO_NOTAS)
                    pitch = snap_to_scale(pitch_raw) 
                else:
                    pitch = ESCALA_BASE
                
                pitch_melodia = int(pitch)
                
                # 2. DURACIÓN (RITMO DE CARRERA)
                speed_factor = max(0, 1 - (avg_speed / VELOCIDAD_MAX_PARA_DURACION))
                duration = DURACION_MINIMA_NOTA + (speed_factor * (4.0 - DURACION_MINIMA_NOTA))

                # 3. PERCUSIÓN (CADENCIA)
                cadence_range = MAX_CADENCE - MIN_CADENCE
                percussion_pitch_range = MAX_PERCUSION_PITCH - MIN_PERCUSION_PITCH
                
                if cadence_range > 0:
                    cadence_position = (cadence - MIN_CADENCE) / cadence_range
                    percussion_pitch = MIN_PERCUSION_PITCH + round(cadence_position * percussion_pitch_range)
                else:
                    percussion_pitch = MIN_PERCUSION_PITCH
                
                percussion_pitch = int(percussion_pitch)
                
                # AÑADIR NOTAS A TRACKS
                midifile.addNote(TRACK_MELODIA, 0, pitch_melodia, time, duration, 100)
                midifile.addNote(TRACK_PERCUSION, CANAL_PERCUSION, percussion_pitch, time, duration, PERCUSION_VELOCITY)

                # Actualizar variables para la siguiente nota
                next_note_distance += DISTANCE_STEP_M
                last_point_time = p_curr.time
                time += duration
        
        # Límite de seguridad
        if time >= 1000: break

    # Guardar MIDI en un buffer en memoria para la descarga
    midi_buffer = io.BytesIO()
    midifile.writeFile(midi_buffer)
    midi_buffer.seek(0)
    return midi_buffer

# --- FUNCIÓN PRINCIPAL DE STREAMLIT ---
def main():
    st.set_page_config(page_title="Trail Sonification App", layout="centered")
    st.title("🎶 Trail Sonification (GPX a Música)")
    st.markdown("Transforma tu **altitud** en **melodía** y tu **ritmo** en **cadencia**.")

    st.markdown("---")
    
    # --- 1. Entrada de Archivo ---
    uploaded_file = st.file_uploader(
        "Sube tu archivo GPX (El archivo debe ser un archivo .gpx)", 
        type=["gpx"]
    )

    st.sidebar.header("Ajustes Musicales")
    
    # --- 2. Sliders ---
    target_minutes = st.sidebar.slider(
        "Duración Total de la Canción (min)", 
        min_value=0.5, max_value=5.0, value=1.0, step=0.1
    )
    tempo = st.sidebar.slider(
        "Tempo Base (BPM)", 
        min_value=60, max_value=180, value=100, step=10
    )
    
    st.sidebar.markdown("---")
    st.sidebar.info("El sistema escala la ruta completa a la duración deseada.")


    if uploaded_file is not None:
        # 3. Procesamiento
        gpx_data_content = uploaded_file.read()
        
        with st.spinner('Procesando datos y componiendo...'):
            try:
                # Llama a la lógica de sonificación
                midi_buffer = generate_midi_file(gpx_data_content, target_minutes, tempo)
                
                # 4. Botón de Descarga
                st.success("¡Composición finalizada!")
                
                st.download_button(
                    label="Descargar Archivo MIDI (.mid)",
                    data=midi_buffer,
                    file_name=f"trail_music_{target_minutes}min_{tempo}bpm.mid",
                    mime="audio/midi"
                )
                
            except Exception as e:
                st.error("Ocurrió un error durante la generación de la música.")
                st.exception(e) # Muestra el error para depuración
                
if __name__ == "__main__":
