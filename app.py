# Importar bibliotecas necesarias

import gpxpy

import gpxpy.gpx

from midiutil.MidiFile import MIDIFile

import math

from google.colab import files

import io



print("-" * 30)



# --- FUNCIÓN PARA CARGAR EL ARCHIVO (Paso 2) ---

def cargar_gpx():

"""Permite al usuario subir un archivo y lo parsea como GPX, manejando errores de codificación."""

print("Paso 2: Sube tu archivo GPX de Strava.")


uploaded = files.upload()


if not uploaded:

print("No se ha subido ningún archivo. Cancelando.")

return None



file_name = list(uploaded.keys())[0]

gpx_data = uploaded[file_name]


# Manejo de codificación

try:

gpx_content = io.StringIO(gpx_data.decode('utf-8'))

except UnicodeDecodeError:

print("Error de decodificación UTF-8. Intentando con LATIN-1...")

gpx_content = io.StringIO(gpx_data.decode('latin-1'))


try:

gpx = gpxpy.parse(gpx_content)

return gpx

except Exception as e:

print(f"Error al parsear el archivo GPX: {e}")

return None



# --- AJUSTES DE MAPEO ---

# ...

# 🎯 CLAVE: Define la duración total deseada para la canción

TARGET_SONG_MINUTES = 0.4 # Cámbiar a un valor más pequeño, como 0.8 o 0.5.

# ...



# 🥁 Ajustes para Percusión (Basados en Cadencia)

TRACK_PERCUSION = 1

CANAL_PERCUSION = 9

PERCUSION_VELOCITY = 100

# Rango de notas MIDI para percusión (35=Bass Drum, 81=Open Triangle)

MIN_PERCUSION_PITCH = 35

MAX_PERCUSION_PITCH = 81

# Rango esperado de cadencia real (para mapeo): 60 a 200 pasos/min

MIN_CADENCE = 60.0

MAX_CADENCE = 200.0





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





# --- FUNCIÓN DE CÁLCULO Y MAPEO (Paso 3, con Cadencia Real) ---

def sonificar_gpx_dinamico(gpx, tempo=TEMPO_BASE_BPM):


if not gpx.tracks: return None

print("-" * 30)

print(f"Paso 3: Generando Melodía (Altitud/Ritmo) y Percusión (Cadencia Real)...")



# 1. PRE-CÁLCULO (Cálculo de rango de altitud y distancia total)

all_elevations = []

segment = gpx.tracks[0].segments[0]

total_distance_m = 0.0

for i in range(len(segment.points)):

p_curr = segment.points[i]

if p_curr.elevation is not None: all_elevations.append(p_curr.elevation)

if i > 0:

p_prev = segment.points[i-1]

total_distance_m += p_curr.distance_3d(p_prev) if p_prev.distance_3d(p_curr) is not None else 0


ele_min = min(all_elevations)

ele_max = max(all_elevations)

ele_range = ele_max - ele_min


# Cálculo Dinámico del Paso de Muestreo

notes_needed = TARGET_SONG_MINUTES * tempo

DISTANCE_STEP_M = max(5.0, total_distance_m / notes_needed)


print(f"Distancia Total: {total_distance_m/1000.0:.2f} km. Muestreo cada: {DISTANCE_STEP_M:.2f} metros/nota.")


# 2. Inicialización MIDI

midifile = MIDIFile(2) # 2 Tracks: 0 (Melodía), 1 (Percusión)

midifile.addTempo(0, 0, tempo)

midifile.addTempo(1, 0, tempo)

midifile.addProgramChange(0, 0, 0, 0) # Track 0: Piano (o cualquier Melodía)


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


# --- CALCULAR VELOCIDAD Y CADENCIA ---

if last_point_time is None:

avg_speed = 1.67

cadence = MIN_CADENCE

else:

delta_time_segment = (p_curr.time - last_point_time).total_seconds()

avg_speed = DISTANCE_STEP_M / delta_time_segment if delta_time_segment > 0 else VELOCIDAD_MAX_PARA_DURACION


# 🎯 CLAVE: Lectura de Cadencia Real (si está presente)

if hasattr(p_curr, 'extensions') and p_curr.extensions and hasattr(p_curr.extensions[0], 'cadence'):

cadence = p_curr.extensions[0].cadence

cadence = max(MIN_CADENCE, min(MAX_CADENCE, cadence)) # Limitar el valor

else:

# Si no hay dato de cadencia, la estimamos a partir de la velocidad

# (Estimación simple: 180 SPM a 3m/s, 100 SPM a 1m/s)

cadence = 100 + (avg_speed * 20)

cadence = max(MIN_CADENCE, min(MAX_CADENCE, cadence))



# --- CÁLCULO DE DURACIÓN (Ritmo de Carrera) ---

speed_factor = max(0, 1 - (avg_speed / VELOCIDAD_MAX_PARA_DURACION))

duration = DURACION_MINIMA_NOTA + (speed_factor * (4.0 - DURACION_MINIMA_NOTA))



# --- TRACK 0: MELODÍA (Altitud) ---

if ele_range > 0:

relative_position = (p_curr.elevation - ele_min) / ele_range

pitch_raw = pitch_base + (relative_position * RANGO_NOTAS)

pitch = snap_to_scale(pitch_raw)

else:

pitch = ESCALA_BASE


pitch = int(pitch)

midifile.addNote(0, 0, pitch, time, duration, 100)



# --- TRACK 1: PERCUSIÓN (Cadencia Real) ---


# Mapeamos la Cadencia a un tono de percusión (pitch)

cadence_range = MAX_CADENCE - MIN_CADENCE

percussion_pitch_range = MAX_PERCUSION_PITCH - MIN_PERCUSION_PITCH


# Interpolación: Cadencia Baja -> Sonido Grave (Bass Drum), Cadencia Alta -> Sonido Agudo (Hi-Hat)

if cadence_range > 0:

cadence_position = (cadence - MIN_CADENCE) / cadence_range

percussion_pitch = MIN_PERCUSION_PITCH + round(cadence_position * percussion_pitch_range)

else:

percussion_pitch = MIN_PERCUSION_PITCH


percussion_pitch = int(percussion_pitch)


# La percusión se toca con la misma duración que la nota principal, pero en el canal 9

midifile.addNote(TRACK_PERCUSION, CANAL_PERCUSION, percussion_pitch, time, duration, PERCUSION_VELOCITY)



# 🎯 Actualizamos variables para la siguiente nota

next_note_distance += DISTANCE_STEP_M

last_point_time = p_curr.time

time += duration


if time >= DURACION_MAXIMA_BEATS: break



print(f"Generación de notas completada. Duración musical final: {time / tempo * 60:.2f} minutos.")

return midifile



# --- FUNCIÓN DE DESCARGA (Paso 4) --- (Código sin cambios)

def descargar_midi(midifile, filename="trail_sonificado_cadencia_real.mid"):

if not midifile: return

print("-" * 30)

print(f"Paso 4: Descargando el archivo '{filename}'...")

with open(filename, "wb") as output_file:

midifile.writeFile(output_file)

files.download(filename)

print(f"¡Archivo '{filename}' generado y listo para reproducir!")





# --- INICIO DEL FLUJO DE TRABAJO ---

gpx_data = cargar_gpx()



if gpx_data:

midi_file_object = sonificar_gpx_dinamico(gpx_data)

descargar_midi(midi_file_object)
