import os
import sys
import subprocess
import shutil
from pathlib import Path
import math
import json
import re
import time

# Try to import moviepy, scan Termux if not found
MOVIEPY_AVAILABLE = False
MOVIEPY_ERROR = None

def find_moviepy_in_termux():
    """Scan Termux directories to find moviepy"""
    termux_paths = [
        "/data/data/com.termux/files/usr/lib/python3.11/site-packages",
        "/data/data/com.termux/files/usr/lib/python3.10/site-packages",
        "/data/data/com.termux/files/usr/lib/python3.9/site-packages",
        "/data/data/com.termux/files/usr/lib/python3/site-packages",
        "/data/data/com.termux/files/home/.local/lib/python3.11/site-packages",
        "/data/data/com.termux/files/home/.local/lib/python3.10/site-packages",
        "/data/data/com.termux/files/home/.local/lib/python3.9/site-packages",
    ]
    
    # Also check current Python's site-packages
    try:
        import site
        termux_paths.extend(site.getsitepackages())
        termux_paths.append(site.getusersitepackages())
    except:
        pass
    
    for path in termux_paths:
        if os.path.exists(path):
            moviepy_path = os.path.join(path, "moviepy")
            if os.path.isdir(moviepy_path):
                return moviepy_path
    
    # Deep scan of Termux
    termux_base = "/data/data/com.termux/files"
    if os.path.exists(termux_base):
        try:
            for root, dirs, files in os.walk(termux_base):
                if "moviepy" in dirs:
                    moviepy_path = os.path.join(root, "moviepy")
                    # Check if it's a valid Python package
                    if os.path.exists(os.path.join(moviepy_path, "__init__.py")) or \
                       os.path.exists(os.path.join(moviepy_path, "editor.py")):
                        return moviepy_path
                # Limit search depth
                if root.count(os.sep) - termux_base.count(os.sep) > 10:
                    dirs.clear()
        except PermissionError:
            pass
    
    return None

try:
    import moviepy
    import moviepy.editor
    MOVIEPY_AVAILABLE = True
except ImportError as e:
    MOVIEPY_ERROR = str(e)
    # Try to find moviepy in Termux
    moviepy_location = find_moviepy_in_termux()
    if moviepy_location:
        # Try adding parent directory to path and import again
        parent_dir = os.path.dirname(moviepy_location)
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)
        try:
            import moviepy
            import moviepy.editor
            MOVIEPY_AVAILABLE = True
            MOVIEPY_ERROR = None
        except ImportError as e2:
            MOVIEPY_ERROR = f"Found at {moviepy_location} but import failed: {e2}"

# Fixed pitch ratio: 2^(1/12) = 1 semitone up
FIXED_PITCH_RATIO = 1.059463094352953

# Default text size
DEFAULT_TEXT_SIZE = 111

# Default watermark size
DEFAULT_WATERMARK_SIZE = 60

# Target speed ratio
TARGET_SPEED_RATIO = 2.0

# Speed ratio tolerance (Changed to guaranteed x2 ~rubix)
SPEED_RATIO_TOLERANCE = 0

# Maximum retry attempts for speed correction
MAX_SPEED_RETRIES = 3

def check_dependencies():
    """Check if required dependencies are installed"""
    if not shutil.which('ffmpeg'):
        raise SystemError("ffmpeg is not installed or not in PATH. Please install ffmpeg first.")
    
    if not shutil.which('ffprobe'):
        raise SystemError("ffprobe is not installed or not in PATH. Please install ffprobe first.")
    
    print("✓ FFmpeg found")
    print("✓ FFprobe found")
    
    if MOVIEPY_AVAILABLE:
        print("✓ MoviePy available")
    else:
        print(f"⚠ MoviePy NOT available")
        if MOVIEPY_ERROR:
            print(f"  Error: {MOVIEPY_ERROR}")
    
    result = subprocess.run(['ffmpeg', '-filters'], capture_output=True, text=True)
    has_rubberband = 'rubberband' in result.stdout
    has_loudnorm = 'loudnorm' in result.stdout
    
    if has_rubberband:
        print("✓ Rubberband filter available")
    else:
        print("⚠ Rubberband filter NOT available")
    
    if has_loudnorm:
        print("✓ Loudnorm filter available")
    else:
        print("⚠ Loudnorm filter NOT available")
    
    return has_rubberband, has_loudnorm

def get_ffmpeg_version():
    """Get ffmpeg version"""
    try:
        result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True)
        version_line = result.stdout.split('\n')[0]
        return version_line
    except:
        return "Unknown"

def get_available_codecs():
    """Get list of available video codecs"""
    try:
        result = subprocess.run(['ffmpeg', '-codecs'], capture_output=True, text=True)
        codecs_output = result.stdout
        
        available = {
            'libx264': 'libx264' in codecs_output or 'H.264' in codecs_output,
            'h264': 'h264' in codecs_output.lower(),
            'mpeg4': 'mpeg4' in codecs_output,
            'libx265': 'libx265' in codecs_output,
        }
        
        print(f"  Available codecs: {[k for k, v in available.items() if v]}")
        return available
        
    except Exception as e:
        print(f"  Warning: Could not detect codecs: {e}")
        return {'libx264': True, 'mpeg4': True}

def select_codec_configs(preset='fast'):
    """Select codec configurations to try"""
    available = get_available_codecs()
    
    configs = []
    
    if available.get('libx264') or available.get('h264'):
        configs.append({
            'name': 'H.264 Baseline',
            'codec': 'libx264',
            'params': ['-profile:v', 'baseline', '-level', '3.0', '-pix_fmt', 'yuv420p', '-preset', preset]
        })
    
    if available.get('libx264'):
        configs.append({
            'name': 'H.264 Main',
            'codec': 'libx264',
            'params': ['-profile:v', 'main', '-pix_fmt', 'yuv420p', '-preset', preset]
        })
        configs.append({
            'name': 'H.264 Ultrafast',
            'codec': 'libx264',
            'params': ['-pix_fmt', 'yuv420p', '-preset', 'ultrafast']
        })
    
    if available.get('mpeg4'):
        configs.append({
            'name': 'MPEG4',
            'codec': 'mpeg4',
            'params': ['-q:v', '5', '-pix_fmt', 'yuv420p']
        })
    
    configs.append({
        'name': 'Fallback',
        'codec': 'libx264',
        'params': ['-pix_fmt', 'yuv420p']
    })
    
    return configs

def validate_video_file(file_path):
    """Validate video file"""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Video file not found: {file_path}")
    
    if not os.path.isfile(file_path):
        raise ValueError(f"Path is not a file: {file_path}")
    
    file_size = os.path.getsize(file_path)
    if file_size < 1000:
        raise ValueError(f"File too small ({file_size} bytes)")
    
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 
             'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', file_path],
            capture_output=True, text=True, timeout=10
        )
        
        if result.returncode != 0:
            raise ValueError("Cannot read video file")
        
        duration = float(result.stdout.strip())
        if duration <= 0:
            raise ValueError("Video has no duration")
        
        return True
        
    except subprocess.TimeoutExpired:
        raise ValueError("Timeout validating video")
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Cannot validate video: {e}")

def get_video_info(file_path):
    """Get video information including frame rate"""
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', file_path],
            capture_output=True, text=True
        )
        
        if result.returncode != 0:
            raise ValueError("FFprobe failed")
        
        probe = json.loads(result.stdout)
        
        video_stream = next((s for s in probe.get('streams', []) if s.get('codec_type') == 'video'), None)
        audio_stream = next((s for s in probe.get('streams', []) if s.get('codec_type') == 'audio'), None)
        
        fps = 30.0
        if video_stream:
            fps_str = video_stream.get('r_frame_rate', '30/1')
            if '/' in fps_str:
                num, den = fps_str.split('/')
                if int(den) > 0:
                    fps = int(num) / int(den)
            else:
                fps = float(fps_str)
        
        info = {
            'duration': float(probe.get('format', {}).get('duration', 0)),
            'size': int(probe.get('format', {}).get('size', 0)),
            'bitrate': int(probe.get('format', {}).get('bit_rate', 0) or 0),
            'video_codec': video_stream.get('codec_name', 'unknown') if video_stream else None,
            'audio_codec': audio_stream.get('codec_name', 'unknown') if audio_stream else None,
            'width': int(video_stream.get('width', 0)) if video_stream else 0,
            'height': int(video_stream.get('height', 0)) if video_stream else 0,
            'has_audio': audio_stream is not None,
            'fps': fps
        }
        return info
        
    except Exception as e:
        print(f"  Warning: Could not get video info: {e}")
        return {'duration': 0, 'size': 0, 'bitrate': 0, 'has_audio': True, 'fps': 30.0}

def get_audio_volume(file_path):
    """Get mean audio volume in dB"""
    try:
        result = subprocess.run(
            ['ffmpeg', '-i', file_path, '-af', 'volumedetect', '-vn', '-f', 'null', '-'],
            capture_output=True, text=True
        )
        
        for line in result.stderr.split('\n'):
            if 'mean_volume' in line:
                parts = line.split('mean_volume:')
                if len(parts) > 1:
                    volume_str = parts[1].strip().split(' ')[0]
                    return float(volume_str)
        
        return -20.0
        
    except Exception as e:
        print(f"  Warning: Could not detect volume: {e}")
        return -20.0

def format_power_notation(number):
    """Format large numbers in scientific notation"""
    if number < 1_000_000:
        return str(number)
    else:
        exponent = math.floor(math.log10(number))
        mantissa = number / (10 ** exponent)
        return f"{mantissa:.2f} * 10^{exponent}"

def get_movies_directories():
    """Get list of accessible directories in movies folder"""
    possible_paths = [
        "/data/data/com.termux/files/home/storage/movies",
        "/data/data/com.termux/files/home/storage/shared/Movies",
        "/storage/emulated/0/Movies",
        "/sdcard/Movies",
        os.path.expanduser("~/Movies"),
        os.path.join(os.getcwd(), "Movies")
    ]
    
    movies_path = None
    for path in possible_paths:
        if os.path.exists(path) and os.path.isdir(path):
            movies_path = path
            break
    
    if not movies_path:
        return None, []
    
    directories = []
    try:
        for item in sorted(os.listdir(movies_path)):
            item_path = os.path.join(movies_path, item)
            if os.path.isdir(item_path):
                # Check if accessible
                try:
                    os.listdir(item_path)
                    directories.append((item, item_path))
                except PermissionError:
                    pass
    except Exception as e:
        return movies_path, []
    
    return movies_path, directories

def find_latest_mp4(directory):
    """Find the latest .mp4 file in directory"""
    mp4_files = []
    
    try:
        for root, dirs, files in os.walk(directory):
            for file in files:
                if file.lower().endswith('.mp4'):
                    file_path = os.path.join(root, file)
                    try:
                        mtime = os.path.getmtime(file_path)
                        size = os.path.getsize(file_path)
                        if size > 1000:  # At least 1KB
                            mp4_files.append((file_path, mtime, size))
                    except:
                        pass
    except Exception as e:
        raise ValueError(f"Cannot access directory: {e}")
    
    if not mp4_files:
        raise FileNotFoundError("No .mp4 files found in directory")
    
    # Sort by modification time, newest first
    mp4_files.sort(key=lambda x: x[1], reverse=True)
    
    latest_file = mp4_files[0][0]
    
    # Validate the file
    try:
        validate_video_file(latest_file)
    except Exception as e:
        raise ValueError(f"Latest video file is corrupted or inaccessible: {e}")
    
    return latest_file

def select_video_from_movies():
    """Let user select video from movies directory"""
    movies_path, directories = get_movies_directories()
    
    if not movies_path:
        raise FileNotFoundError("Movies folder not found in any known location")
    
    if not directories:
        raise FileNotFoundError(f"No accessible directories found in: {movies_path}")
    
    print(f"\n{'='*60}")
    print(f"VIDEO EDITOR FOLDERS")
    print(f"{'='*60}")
    print(f"Location: {movies_path}\n")
    
    for i, (name, path) in enumerate(directories, 1):
        # Count mp4 files
        try:
            mp4_count = sum(1 for f in os.listdir(path) if f.lower().endswith('.mp4'))
            print(f"  [{i}] {name} ({mp4_count} mp4 files)")
        except:
            print(f"  [{i}] {name}")
    
    print()
    fNum = len(directories)
    
    while True:
        try:
            selection = input(f"Select your video editor folder (1-{fNum}): ").strip()
            
            if not selection.isdigit():
                raise ValueError("Please enter a number")
            
            sel_num = int(selection)
            if sel_num < 1 or sel_num > fNum:
                raise ValueError(f"Please enter a number between 1 and {fNum}")
            
            selected_name, selected_path = directories[sel_num - 1]
            
            print(f"\n  Selected: {selected_name}")
            print(f"  Searching for latest .mp4 file...")
            
            latest_file = find_latest_mp4(selected_path)
            
            print(f"  ✓ Found: {os.path.basename(latest_file)}")
            
            return latest_file
            
        except FileNotFoundError as e:
            print(f"  ❌ Error: {e}")
            retry = input("  Try another folder? (Y/N): ").strip().upper()
            if retry != 'Y':
                raise
        except ValueError as e:
            print(f"  ❌ Error: {e}")
            continue

def print_progress_bar(current, total, bar_length=50):
    """Print progress bar with status text"""
    if total == 0:
        return
    
    progress = current / total
    filled = int(bar_length * progress)
    bar = '█' * filled + '░' * (bar_length - filled)
    percent = progress * 100
    
    # Clear previous lines and print new progress
    sys.stdout.write('\033[2K\033[1A\033[2K\r')  # Clear current and previous line
    print(f'[{bar}] {percent:.1f}%')
    print(f'{current} out of {total} done.', end='', flush=True)

def init_progress_bar():
    """Initialize progress bar display"""
    print()  # Empty line for progress bar
    print()  # Empty line for status text

def finish_progress_bar():
    """Finish progress bar and move to new line"""
    print()  # New line after progress

def get_user_inputs(use_editor_selection=False):
    """Get and validate user inputs"""
    try:
        if use_editor_selection:
            video_path = select_video_from_movies()
        else:
            video_path = input("Video File Location?: ").strip()
            if not video_path:
                raise ValueError("Video file location cannot be empty")
            
            video_path = video_path.strip('"').strip("'")
            validate_video_file(video_path)
        
        exports_str = input("How much exports?: ").strip()
        if not exports_str.isdigit():
            raise ValueError("Number of exports must be a positive integer")
        num_exports = int(exports_str)
        if num_exports <= 0:
            raise ValueError("Number of exports must be greater than 0")
        if num_exports > 20:
            confirm = input(f"Warning: {num_exports} exports may take long. Continue? (y/n): ")
            if confirm.lower() != 'y':
                raise ValueError("Export cancelled")
        
        start_str = input("Starting Number?: ").strip()
        if not start_str.isdigit():
            raise ValueError("Starting number must be a positive integer")
        start_num = int(start_str)
        if start_num < 0:
            raise ValueError("Starting number must be non-negative")
        
        pitch_input = input("Set Pitch Increase (N/Y)?: ").strip().upper()
        if pitch_input not in ['N', 'Y']:
            print("  Invalid input, defaulting to N")
            enable_pitch = False
        else:
            enable_pitch = pitch_input == 'Y'
        
        # Text size input
        text_size_input = input("Change text size to num?: ").strip()
        if text_size_input == '' or not text_size_input.isdigit():
            if text_size_input != '':
                print("  Error!: invalid size.")
            text_size = DEFAULT_TEXT_SIZE
        else:
            text_size = int(text_size_input)
            if text_size <= 0:
                print("  Error!: invalid size.")
                text_size = DEFAULT_TEXT_SIZE
        
        # Watermark size input
        watermark_size_input = input("Resize watermark to?: ").strip()
        if watermark_size_input and watermark_size_input.isdigit():
            watermark_size = int(watermark_size_input)
            if watermark_size <= 0:
                print("  Error!: invalid watermark size.")
                watermark_size = DEFAULT_WATERMARK_SIZE
        else:
            # Has letter or is empty - don't change
            watermark_size = DEFAULT_WATERMARK_SIZE
        
        # Color mode input
        color_mode_input = input("Use Color Mode (N/Y)?: ").strip().upper()
        if color_mode_input not in ['N', 'Y']:
            print("  Invalid input, defaulting to N")
            enable_color_mode = False
        else:
            enable_color_mode = color_mode_input == 'Y'
        
        # Fast exports input
        fast_export_input = input("Use fast exports? (N/Y/Z/U)?: ").strip().upper()
        if fast_export_input == 'Y':
            preset = 'veryfast'
        elif fast_export_input == 'Z':
            preset = 'superfast'
        elif fast_export_input == 'U':
            preset = 'ultrafast'
        elif fast_export_input == 'N':
            preset = 'fast'
        else:
            print("  Invalid input, defaulting to fast")
            preset = 'fast'
        
        return video_path, num_exports, start_num, enable_pitch, text_size, enable_color_mode, preset, watermark_size
        
    except Exception as e:
        raise e

def create_exports_folder():
    """Create Exports folder"""
    termux_downloads = "/data/data/com.termux/files/home/storage/downloads"
    
    if os.path.exists(termux_downloads):
        exports_dir = Path(termux_downloads) / "Exports"
    else:
        print("Warning: Termux not detected. Using current directory.")
        exports_dir = Path("Exports")
    
    if not exports_dir.exists():
        exports_dir.mkdir(parents=True, exist_ok=True)
        print(f"Created 'Exports' directory at: {exports_dir}")
    else:
        print(f"'Exports' directory exists at: {exports_dir}")
    
    return exports_dir

def get_unique_filename(exports_dir, base_name):
    """Get unique filename with oID if exists"""
    output_path = os.path.join(exports_dir, f"{base_name}.mp4")
    
    if not os.path.exists(output_path):
        return output_path, base_name
    
    oID = 1
    while True:
        unique_name = f"{base_name}-{oID}"
        unique_path = os.path.join(exports_dir, f"{unique_name}.mp4")
        if not os.path.exists(unique_path):
            return unique_path, unique_name
        oID += 1
        if oID > 999:
            raise RuntimeError("Too many duplicate files")

def escape_text_for_ffmpeg(text):
    """Escape text for ffmpeg drawtext"""
    text = text.replace('\\', '\\\\')
    text = text.replace(':', '\\:')
    text = text.replace("'", "\\'")
    text = text.replace('[', '\\[')
    text = text.replace(']', '\\]')
    text = text.replace(',', '\\,')
    text = text.replace(';', '\\;')
    return text

def verify_output_file(file_path, min_size_kb=0):
    """Verify output file is valid (no minimum size limit)"""
    if not os.path.exists(file_path):
        return False, "File not created"
    
    if min_size_kb > 0:
        size_kb = os.path.getsize(file_path) / 1024
        if size_kb < min_size_kb:
            return False, f"File too small ({size_kb:.1f} KB < {min_size_kb} KB)"
    
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 
             'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', file_path],
            capture_output=True, text=True, timeout=5
        )
        
        if result.returncode != 0:
            return False, "FFprobe failed"
        
        duration = float(result.stdout.strip())
        if duration <= 0:
            return False, "Invalid duration"
        
        return True, "Valid"
        
    except Exception as e:
        return False, f"Error: {e}"

def find_existing_exports(exports_dir):
    """Find all existing export files in the Exports directory"""
    export_files = []
    pattern = re.compile(r'^export-(\d+)(?:-\d+)?\.mp4$')
    
    if not os.path.exists(exports_dir):
        return []
    
    for filename in os.listdir(exports_dir):
        match = pattern.match(filename)
        if match:
            export_num = int(match.group(1))
            file_path = os.path.join(exports_dir, filename)
            # Verify it's a valid video file
            if os.path.isfile(file_path) and os.path.getsize(file_path) > 1000:
                export_files.append((export_num, filename, file_path))
    
    # Sort by export number
    export_files.sort(key=lambda x: x[0])
    
    return export_files

def process_video_moviepy(input_path, output_path, export_num, iteration, enable_pitch, 
                          original_fps, has_rubberband, text_size, enable_color_mode, preset):
    """Process video using moviepy"""
    if not MOVIEPY_AVAILABLE:
        raise RuntimeError(f"MoviePy not available: {MOVIEPY_ERROR}")
    
    video = None
    sped_video = None
    final_video = None
    txt_clip = None
    result_video = None
    temp_files = []
    
    try:
        export_pow = 2 ** export_num
        power_text = format_power_notation(export_pow)
        text_string = f"{export_num} - {power_text}"
        
        temp_dir = os.path.dirname(output_path)
        
        # Load video
        video = moviepy.editor.VideoFileClip(input_path)
        has_audio = video.audio is not None
        
        if enable_pitch:
            # Speed up with pitch change (natural speedup behavior)
            sped_video = video.speedx(2)
        else:
            # Speed up without pitch change
            if has_audio:
                # Speed up video without audio first
                video_no_audio = video.without_audio()
                sped_video_only = video_no_audio.speedx(2)
                
                # Extract and process audio with ffmpeg to maintain pitch
                temp_audio_in = os.path.join(temp_dir, f"temp_audio_in_{export_num}_{os.getpid()}.aac")
                temp_audio_out = os.path.join(temp_dir, f"temp_audio_out_{export_num}_{os.getpid()}.aac")
                temp_files.extend([temp_audio_in, temp_audio_out])
                
                # Extract audio using ffmpeg
                cmd_extract = [
                    'ffmpeg', '-i', input_path,
                    '-vn', '-acodec', 'aac', '-y', temp_audio_in
                ]
                subprocess.run(cmd_extract, capture_output=True)
                
                # Speed up audio without pitch change
                if has_rubberband:
                    audio_filter = "rubberband=tempo=2.0"
                else:
                    audio_filter = "atempo=2.0"
                
                cmd_audio = [
                    'ffmpeg', '-i', temp_audio_in,
                    '-af', audio_filter,
                    '-acodec', 'aac', '-y', temp_audio_out
                ]
                subprocess.run(cmd_audio, capture_output=True)
                
                # Load processed audio and combine
                if os.path.exists(temp_audio_out):
                    processed_audio = moviepy.editor.AudioFileClip(temp_audio_out)
                    sped_video = sped_video_only.set_audio(processed_audio)
                else:
                    # Fallback: no audio
                    sped_video = sped_video_only
            else:
                # No audio, just speed up
                sped_video = video.speedx(2)
        
        # Concatenate (duplicate)
        final_video = moviepy.editor.concatenate_videoclips([sped_video, sped_video])
        
        # Create text clip
        try:
            txt_clip = moviepy.editor.TextClip(
                text_string,
                fontsize=text_size,
                color='red',
                stroke_color='blue',
                stroke_width=3,
                font='DejaVu-Sans-Bold'
            )
        except:
            # Fallback font
            try:
                txt_clip = moviepy.editor.TextClip(
                    text_string,
                    fontsize=text_size,
                    color='red',
                    stroke_color='blue',
                    stroke_width=3
                )
            except:
                txt_clip = moviepy.editor.TextClip(
                    text_string,
                    fontsize=min(text_size, 80),
                    color='red'
                )
        
        txt_clip = txt_clip.set_position((20, final_video.h - 150)).set_duration(final_video.duration)
        
        # Composite
        result_video = moviepy.editor.CompositeVideoClip([final_video, txt_clip])
        
        # Write to temp file first if color mode is enabled
        if enable_color_mode:
            temp_output = os.path.join(temp_dir, f"temp_nocolor_{export_num}_{os.getpid()}.mp4")
            temp_files.append(temp_output)
            
            result_video.write_videofile(
                temp_output,
                fps=original_fps,
                codec='libx264',
                audio_codec='aac',
                preset=preset,
                verbose=False,
                logger=None
            )
            
            # Apply hue shift using ffmpeg
            cmd_hue = [
                'ffmpeg', '-i', temp_output,
                '-vf', 'hue=h=25',
                '-c:v', 'libx264',
                '-preset', preset,
                '-c:a', 'copy',
                '-y', output_path
            ]
            subprocess.run(cmd_hue, capture_output=True)
        else:
            # Write output directly
            result_video.write_videofile(
                output_path,
                fps=original_fps,
                codec='libx264',
                audio_codec='aac',
                preset=preset,
                verbose=False,
                logger=None
            )
        
        return True
        
    except Exception as e:
        raise RuntimeError(f"MoviePy error: {e}")
        
    finally:
        # Cleanup clips
        for clip in [video, sped_video, final_video, txt_clip, result_video]:
            if clip is not None:
                try:
                    clip.close()
                except:
                    pass
        
        # Cleanup temp files
        for temp_file in temp_files:
            if temp_file and os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except:
                    pass

def build_speedup_command(input_path, output_path, tempo, video_pts, enable_pitch, 
                          has_rubberband, has_audio, volume_adjustment, original_fps, preset):
    """Build ffmpeg command for speedup with given tempo"""
    
    if enable_pitch and has_rubberband and has_audio:
        # Single rubberband filter for both pitch and tempo
        audio_filter = f"rubberband=tempo={tempo}:pitch={FIXED_PITCH_RATIO}:pitchq=speed,volume={volume_adjustment}dB"
        
        cmd = [
            'ffmpeg', '-i', input_path,
            '-filter_complex',
            f'[0:v]setpts={video_pts}*PTS[v];[0:a]{audio_filter}[a]',
            '-map', '[v]',
            '-map', '[a]',
            '-c:v', 'libx264',
            '-preset', preset,
            '-crf', '23',
            '-pix_fmt', 'yuv420p',
            '-r', str(original_fps),
            '-c:a', 'aac',
            '-b:a', '128k',
            '-ar', '44100',
            '-shortest',
            '-y', output_path
        ]
    elif has_rubberband and has_audio:
        # No pitch, just tempo with rubberband
        audio_filter = f"rubberband=tempo={tempo}:pitchq=speed,volume={volume_adjustment}dB"
        
        cmd = [
            'ffmpeg', '-i', input_path,
            '-filter_complex',
            f'[0:v]setpts={video_pts}*PTS[v];[0:a]{audio_filter}[a]',
            '-map', '[v]',
            '-map', '[a]',
            '-c:v', 'libx264',
            '-preset', preset,
            '-crf', '23',
            '-pix_fmt', 'yuv420p',
            '-r', str(original_fps),
            '-c:a', 'aac',
            '-b:a', '128k',
            '-ar', '44100',
            '-shortest',
            '-y', output_path
        ]
    elif enable_pitch and not has_rubberband and has_audio:
        # Fallback: atempo + asetrate for pitch
        pitched_rate = int(44100 * FIXED_PITCH_RATIO)
        audio_filter = f"atempo={tempo},asetrate={pitched_rate},aresample=44100,volume={volume_adjustment}dB"
        
        cmd = [
            'ffmpeg', '-i', input_path,
            '-filter_complex',
            f'[0:v]setpts={video_pts}*PTS[v];[0:a]{audio_filter}[a]',
            '-map', '[v]',
            '-map', '[a]',
            '-c:v', 'libx264',
            '-preset', preset,
            '-crf', '23',
            '-pix_fmt', 'yuv420p',
            '-r', str(original_fps),
            '-c:a', 'aac',
            '-b:a', '128k',
            '-ar', '44100',
            '-shortest',
            '-y', output_path
        ]
    elif has_audio:
        # No rubberband, no pitch - just atempo
        audio_filter = f"atempo={tempo},volume={volume_adjustment}dB"
        
        cmd = [
            'ffmpeg', '-i', input_path,
            '-filter_complex',
            f'[0:v]setpts={video_pts}*PTS[v];[0:a]{audio_filter}[a]',
            '-map', '[v]',
            '-map', '[a]',
            '-c:v', 'libx264',
            '-preset', preset,
            '-crf', '23',
            '-pix_fmt', 'yuv420p',
            '-r', str(original_fps),
            '-c:a', 'aac',
            '-b:a', '128k',
            '-ar', '44100',
            '-shortest',
            '-y', output_path
        ]
    else:
        # No audio
        cmd = [
            'ffmpeg', '-i', input_path,
            '-vf', f'setpts={video_pts}*PTS',
            '-c:v', 'libx264',
            '-preset', preset,
            '-crf', '23',
            '-pix_fmt', 'yuv420p',
            '-r', str(original_fps),
            '-an',
            '-y', output_path
        ]
    
    return cmd

def process_video_cumulative(input_path, output_path, export_num, iteration, reference_size_mb, 
                             enable_pitch, has_rubberband, has_loudnorm, target_volume_db, 
                             original_fps, use_moviepy=False, silent=False, text_size=DEFAULT_TEXT_SIZE,
                             enable_color_mode=False, preset='fast'):
    """Process video cumulatively with rubberband tempo+pitch and auto tempo correction"""
    
    if use_moviepy:
        return process_video_moviepy(input_path, output_path, export_num, iteration, 
                                     enable_pitch, original_fps, has_rubberband, text_size,
                                     enable_color_mode, preset)
    
    temp_files = []
    
    try:
        export_pow = 2 ** export_num
        power_text = format_power_notation(export_pow)
        
        if not silent:
            print(f"  Export power (2^{export_num}): {power_text}")
        
        text_string = f"{export_num} - {power_text}"
        text_escaped = escape_text_for_ffmpeg(text_string)
        
        input_info = get_video_info(input_path)
        input_duration = input_info['duration']
        input_size_mb = input_info['size'] / (1024 * 1024)
        has_audio = input_info['has_audio']
        input_fps = input_info.get('fps', original_fps)
        
        expected_sped_duration = input_duration / TARGET_SPEED_RATIO
        expected_final_duration = expected_sped_duration * 2
        
        if not silent:
            print(f"  Input: {input_duration:.2f}s, {input_size_mb:.2f} MB, {input_fps:.2f}fps")
            print(f"  Expected: sped={expected_sped_duration:.2f}s, final={expected_final_duration:.2f}s")
        
        # Volume adjustment
        current_volume = get_audio_volume(input_path) if has_audio else -20.0
        volume_adjustment = target_volume_db - current_volume
        
        if not silent:
            if enable_pitch:
                cumulative_semitones = (iteration + 1) * 1  # 1 semitone per export
                print(f"  Pitch: rubberband tempo=2.0:pitch={FIXED_PITCH_RATIO}")
                print(f"    Cumulative: +{cumulative_semitones} semitones from original")
            print(f"  Volume: {current_volume:.1f}dB -> {target_volume_db:.1f}dB (adjust: {volume_adjustment:+.1f}dB)")
        
        # Temp file paths
        temp_dir = os.path.dirname(output_path)
        temp_sped = os.path.join(temp_dir, f"temp_sped_{export_num}_{os.getpid()}.mp4")
        temp_list = os.path.join(temp_dir, f"temp_list_{export_num}_{os.getpid()}.txt")
        temp_concat = os.path.join(temp_dir, f"temp_concat_{export_num}_{os.getpid()}.mp4")
        
        temp_files = [temp_sped, temp_list, temp_concat]
        
        # Step 1: Speed up by 2x with tempo correction loop
        tempo = TARGET_SPEED_RATIO  # Start with 2.0
        video_pts = 1.0 / tempo  # 0.5 for 2x speed
        
        for attempt in range(MAX_SPEED_RETRIES):
            if attempt == 0:
                if not silent:
                    if enable_pitch and has_rubberband and has_audio:
                        print(f"  Step 1/3: rubberband tempo={tempo}:pitch={FIXED_PITCH_RATIO}...")
                    elif has_rubberband and has_audio:
                        print(f"  Step 1/3: rubberband tempo={tempo} (no pitch)...")
                    elif enable_pitch and not has_rubberband and has_audio:
                        print(f"  Step 1/3: atempo={tempo} + asetrate pitch (fallback)...")
                    elif has_audio:
                        print(f"  Step 1/3: atempo={tempo} (no pitch, no rubberband)...")
                    else:
                        print(f"  Step 1/3: setpts={video_pts}*PTS (no audio)...")
            else:
                if not silent:
                    print(f"    Retry {attempt}: tempo={tempo:.4f}, video_pts={video_pts:.4f}...")
            
            # Remove previous attempt if exists
            if os.path.exists(temp_sped):
                try:
                    os.remove(temp_sped)
                except:
                    pass
            
            # Build and run command
            cmd_speed = build_speedup_command(
                input_path, temp_sped, tempo, video_pts, enable_pitch,
                has_rubberband, has_audio, volume_adjustment, original_fps, preset
            )
            
            result = subprocess.run(cmd_speed, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"Speed-up failed: {result.stderr[-300:]}")
            
            valid, msg = verify_output_file(temp_sped)
            if not valid:
                raise RuntimeError(f"Speed-up invalid: {msg}")
            
            # Check the actual speed ratio
            sped_info = get_video_info(temp_sped)
            actual_sped_duration = sped_info['duration']
            
            speed_ratio = input_duration / actual_sped_duration if actual_sped_duration > 0 else 0
            
            if not silent:
                print(f"    Sped-up: {actual_sped_duration:.2f}s (speed ratio: {speed_ratio:.2f}x)")
            
            # Check if speed ratio is within acceptable range
            min_acceptable = TARGET_SPEED_RATIO * (1 - SPEED_RATIO_TOLERANCE)  # 1.95
            
            if speed_ratio >= min_acceptable:
                # Speed is acceptable
                if not silent and attempt > 0:
                    print(f"    ✓ Speed ratio corrected to {speed_ratio:.2f}x")
                break
            else:
                # Speed is too slow, need to increase tempo
                if attempt < MAX_SPEED_RETRIES - 1:
                    if not silent:
                        print(f"    ⚠ Speed ratio {speed_ratio:.2f}x is below target {TARGET_SPEED_RATIO}x")
                        print(f"    Calculating corrected tempo...")
                    
                    # Calculate correction factor
                    # We need: actual_sped_duration / expected_sped_duration = correction needed
                    correction_factor = actual_sped_duration / expected_sped_duration
                    
                    # New tempo = current tempo * correction factor
                    tempo = tempo * correction_factor
                    video_pts = 1.0 / tempo
                    
                    if not silent:
                        print(f"    Corrected tempo: {tempo:.4f} (video_pts: {video_pts:.4f})")
                else:
                    # Final attempt failed, warn but continue
                    if not silent:
                        print(f"    ⚠ Warning: Could not achieve target speed after {MAX_SPEED_RETRIES} attempts")
                        print(f"    Proceeding with speed ratio: {speed_ratio:.2f}x")
        
        # Step 2: Duplicate
        if not silent:
            print(f"  Step 2/3: Duplicating video...")
        
        abs_temp_sped = os.path.abspath(temp_sped)
        with open(temp_list, 'w') as f:
            f.write(f"file '{abs_temp_sped}'\n")
            f.write(f"file '{abs_temp_sped}'\n")
        
        cmd_concat = [
            'ffmpeg',
            '-f', 'concat',
            '-safe', '0',
            '-i', temp_list,
            '-c', 'copy',
            '-y', temp_concat
        ]
        
        result = subprocess.run(cmd_concat, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Concat failed: {result.stderr[-300:]}")
        
        valid, msg = verify_output_file(temp_concat)
        if not valid:
            raise RuntimeError(f"Concat invalid: {msg}")
        
        concat_info = get_video_info(temp_concat)
        concat_duration = concat_info['duration']
        if not silent:
            print(f"    Concatenated: {concat_duration:.2f}s")
        
        # Step 3: Add text overlay (and color shift if enabled)
        if not silent:
            print(f"  Step 3/3: Adding text and exporting...")
        
        target_size_mb = reference_size_mb * 1.15
        target_bitrate_total = (target_size_mb * 8 * 1024) / concat_duration
        audio_bitrate = 128
        target_video_bitrate = max(500, int(target_bitrate_total - audio_bitrate))
        
        if not silent:
            print(f"    Target: {target_size_mb:.2f} MB, {target_video_bitrate} kbps")
        
        drawtext_filter = (
            f"drawtext=text='{text_escaped}':"
            f"fontcolor=red:"
            f"bordercolor=blue:borderw=3:"
            f"fontsize={text_size}:"
            f"box=1:boxcolor=black@0.12:boxborderw=8:"
            f"x=20:y=h-th-20"
        )
        
        # Add hue shift if color mode is enabled
        if enable_color_mode:
            video_filter = f"{drawtext_filter},hue=h=25"
        else:
            video_filter = drawtext_filter
        
        codec_configs = select_codec_configs(preset)
        success = False
        last_error = None
        
        for codec_config in codec_configs:
            if success:
                break
            
            codec_name = codec_config['name']
            codec = codec_config['codec']
            codec_params = codec_config['params']
            
            if not silent:
                print(f"    Trying {codec_name}...")
            
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except:
                    pass
            
            if concat_info.get('has_audio', True):
                cmd_text = [
                    'ffmpeg', '-i', temp_concat,
                    '-vf', video_filter,
                    '-c:v', codec
                ] + codec_params + [
                    '-b:v', f'{target_video_bitrate}k',
                    '-maxrate', f'{int(target_video_bitrate * 1.5)}k',
                    '-bufsize', f'{int(target_video_bitrate * 2)}k',
                    '-r', str(original_fps),
                    '-c:a', 'aac',
                    '-b:a', '128k',
                    '-movflags', '+faststart',
                    '-max_muxing_queue_size', '1024',
                    '-y', output_path
                ]
            else:
                cmd_text = [
                    'ffmpeg', '-i', temp_concat,
                    '-vf', video_filter,
                    '-c:v', codec
                ] + codec_params + [
                    '-b:v', f'{target_video_bitrate}k',
                    '-maxrate', f'{int(target_video_bitrate * 1.5)}k',
                    '-bufsize', f'{int(target_video_bitrate * 2)}k',
                    '-r', str(original_fps),
                    '-an',
                    '-movflags', '+faststart',
                    '-y', output_path
                ]
            
            result = subprocess.run(cmd_text, capture_output=True, text=True)
            
            if result.returncode == 0:
                valid, msg = verify_output_file(output_path)
                if valid:
                    output_info = get_video_info(output_path)
                    output_size_mb = output_info['size'] / (1024 * 1024)
                    if not silent:
                        print(f"    ✓ {codec_name}: {output_size_mb:.2f} MB")
                    success = True
                else:
                    if not silent:
                        print(f"    ✗ {codec_name}: {msg}")
                    last_error = msg
            else:
                error_msg = result.stderr[-200:] if result.stderr else "Unknown"
                if not silent:
                    print(f"    ✗ {codec_name}: {error_msg}")
                last_error = error_msg
        
        if not success:
            raise RuntimeError(f"All codecs failed: {last_error}")
        
        final_info = get_video_info(output_path)
        final_duration = final_info['duration']
        cumulative_speed = 2 ** (iteration + 1)
        
        if not silent:
            print(f"✓ Export {export_num} completed")
            print(f"    Final duration: {final_duration:.2f}s | Cumulative speed: {cumulative_speed}x")
        
        return True
        
    except Exception as e:
        raise RuntimeError(f"Error: {str(e)}")
        
    finally:
        for temp_file in temp_files:
            if temp_file and os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except:
                    pass

def compile_exports(export_files, exports_dir, original_fps, preset='fast', watermark_size=DEFAULT_WATERMARK_SIZE):
    """Compile all exports into single video with watermark"""
    try:
        print(f"\n{'='*60}")
        print("COMPILING ALL EXPORTS...")
        print(f"{'='*60}")
        
        output_path, output_name = get_unique_filename(exports_dir, "SpeedExp-Compilation")
        
        print(f"  Output: {output_name}.mp4")
        print(f"  Merging {len(export_files)} exports...")
        print(f"  Watermark size: {watermark_size}")
        
        temp_list = os.path.join(exports_dir, f"temp_compile_list_{os.getpid()}.txt")
        
        with open(temp_list, 'w') as f:
            for export_file in export_files:
                abs_path = os.path.abspath(export_file)
                f.write(f"file '{abs_path}'\n")
        
        temp_concat = os.path.join(exports_dir, f"temp_compile_concat_{os.getpid()}.mp4")
        
        print(f"  Step 1/2: Concatenating exports...")
        
        cmd_concat = [
            'ffmpeg',
            '-f', 'concat',
            '-safe', '0',
            '-i', temp_list,
            '-c', 'copy',
            '-y', temp_concat
        ]
        
        result = subprocess.run(cmd_concat, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Compilation concat failed: {result.stderr[-300:]}")
        
        valid, msg = verify_output_file(temp_concat)
        if not valid:
            raise RuntimeError(f"Compilation concat invalid: {msg}")
        
        concat_info = get_video_info(temp_concat)
        print(f"    Total duration: {concat_info['duration']:.2f}s")
        
        print(f"  Step 2/2: Adding watermark...")
        
        watermark_text = escape_text_for_ffmpeg("Made with SpeedExp.py.")
        
        watermark_filter = (
            f"drawtext=text='{watermark_text}':"
            f"fontcolor=white@0.75:"
            f"bordercolor=black@0.75:borderw=2:"
            f"fontsize={watermark_size}:"
            f"box=1:boxcolor=orange@0.75:boxborderw=5:"
            f"x=w-tw-20:y=20"
        )
        
        codec_configs = select_codec_configs(preset)
        success = False
        
        for codec_config in codec_configs:
            if success:
                break
            
            codec_name = codec_config['name']
            codec = codec_config['codec']
            codec_params = codec_config['params']
            
            print(f"    Trying {codec_name}...")
            
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except:
                    pass
            
            cmd_watermark = [
                'ffmpeg', '-i', temp_concat,
                '-vf', watermark_filter,
                '-c:v', codec
            ] + codec_params + [
                '-r', str(original_fps),
                '-c:a', 'aac',
                '-b:a', '128k',
                '-movflags', '+faststart',
                '-max_muxing_queue_size', '1024',
                '-y', output_path
            ]
            
            result = subprocess.run(cmd_watermark, capture_output=True, text=True)
            
            if result.returncode == 0:
                valid, msg = verify_output_file(output_path)
                if valid:
                    print(f"    ✓ {codec_name}")
                    success = True
                else:
                    print(f"    ✗ {codec_name}: {msg}")
            else:
                error_msg = result.stderr[-200:] if result.stderr else "Unknown"
                print(f"    ✗ {codec_name}: {error_msg}")
        
        for temp_file in [temp_list, temp_concat]:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except:
                    pass
        
        if not success:
            raise RuntimeError("Failed to add watermark with all codecs")
        
        final_info = get_video_info(output_path)
        final_size = final_info['size'] / (1024 * 1024)
        
        print(f"\n✓ COMPILATION COMPLETE!")
        print(f"  File: {output_name}.mp4")
        print(f"  Size: {final_size:.2f} MB")
        print(f"  Duration: {final_info['duration']:.2f}s")
        
        return output_path
        
    except Exception as e:
        raise RuntimeError(f"Compilation failed: {str(e)}")

def check_file_size(file_path):
    """Get file size in MB"""
    if os.path.exists(file_path):
        return os.path.getsize(file_path) / (1024 * 1024)
    return 0

def compile_existing_exports_mode(exports_dir, preset='fast'):
    """Handle compilation of existing export files"""
    print(f"\n{'='*60}")
    print("COMPILE EXISTING EXPORTS MODE")
    print(f"{'='*60}")
    
    existing_exports = find_existing_exports(exports_dir)
    
    if not existing_exports:
        print("\n❌ No existing export files found in Exports folder.")
        print(f"   Location: {os.path.abspath(exports_dir)}")
        print("   Expected format: export-N.mp4 or export-N-ID.mp4")
        return False
    
    print(f"\n✓ Found {len(existing_exports)} export file(s):\n")
    
    total_size = 0
    total_duration = 0
    
    for export_num, filename, file_path in existing_exports:
        info = get_video_info(file_path)
        size_mb = info['size'] / (1024 * 1024)
        duration = info['duration']
        total_size += size_mb
        total_duration += duration
        
        pow_val = 2 ** export_num
        pow_display = format_power_notation(pow_val)
        
        print(f"  [{export_num}] {filename}")
        print(f"      Size: {size_mb:.2f} MB | Duration: {duration:.2f}s")
        print(f"      Text: '{export_num} - {pow_display}'")
    
    print(f"\n  {'─'*40}")
    print(f"  Total: {len(existing_exports)} files, {total_size:.2f} MB, {total_duration:.2f}s")
    print(f"  {'─'*40}")
    
    # Get fps from first file
    sample_info = get_video_info(existing_exports[0][2])
    original_fps = sample_info.get('fps', 30.0)
    
    print(f"\n  Detected FPS: {original_fps:.2f}")
    
    # Watermark size input for compile existing mode
    watermark_size_input = input("\nResize watermark to?: ").strip()
    if watermark_size_input and watermark_size_input.isdigit():
        watermark_size = int(watermark_size_input)
        if watermark_size <= 0:
            print("  Error!: invalid watermark size.")
            watermark_size = DEFAULT_WATERMARK_SIZE
    else:
        # Has letter or is empty - don't change
        watermark_size = DEFAULT_WATERMARK_SIZE
    
    confirm = input("\nProceed with compilation? (N/Y): ").strip().upper()
    
    if confirm != 'Y':
        print("Compilation cancelled.")
        return False
    
    # Extract just the file paths for compile_exports
    export_file_paths = [f[2] for f in existing_exports]
    
    compile_exports(export_file_paths, exports_dir, original_fps, preset, watermark_size)
    
    return True

def main():
    """Main function"""
    try:
        print("=== SpeedExp.py - Video Export Tool ===")
        print("Checking dependencies...")
        has_rubberband, has_loudnorm = check_dependencies()
        
        ffmpeg_version = get_ffmpeg_version()
        print(f"  FFmpeg: {ffmpeg_version}")
        print()
        
        termux_path = "/data/data/com.termux/files/home/storage/downloads"
        if os.path.exists(termux_path):
            print(f"✓ Termux detected")
            print(f"  Output: {termux_path}/Exports\n")
        else:
            print("⚠ Using current directory\n")
        
        # Create exports folder first
        exports_dir = create_exports_folder()
        
        # Ask about compiling existing exports
        compile_existing_input = input("\nCompile Existing export files? (N/Y): ").strip().upper()
        
        if compile_existing_input == 'Y':
            # Ask for preset for compilation
            fast_export_input = input("Use fast exports? (N/Y/Z/U)?: ").strip().upper()
            if fast_export_input == 'Y':
                preset = 'veryfast'
            elif fast_export_input == 'Z':
                preset = 'superfast'
            elif fast_export_input == 'U':
                preset = 'ultrafast'
            else:
                preset = 'fast'
            
            result = compile_existing_exports_mode(exports_dir, preset)
            if result:
                print(f"\n{'='*60}")
                print("✓ ALL DONE!")
                print(f"{'='*60}")
                return
            else:
                print("\nContinuing with normal export process...\n")
        elif compile_existing_input != 'N':
            print("  Invalid input, continuing with normal export process...\n")
        
        # Ask about using MoviePy
        use_moviepy = False
        moviepy_input = input("\nUse moviepy? (N/Y): ").strip().upper()
        
        if moviepy_input == 'Y':
            if not MOVIEPY_AVAILABLE:
                print("  ❌ MoviePy is not installed or not found!")
                if MOVIEPY_ERROR:
                    print(f"  Error: {MOVIEPY_ERROR}")
                print("  Install with: pip install moviepy")
                print("  Scanned Termux directories but moviepy was not found.")
                raise SystemError("MoviePy not available. Please install it with: pip install moviepy")
            else:
                print("  ✓ Using MoviePy mode")
                use_moviepy = True
        elif moviepy_input != 'N':
            print("  Invalid input, using FFmpeg...\n")
        
        # Ask about video editor folder selection (available for both modes)
        use_editor_selection = False
        editor_input = input("\nSelect from video editor folders? (N/Y): ").strip().upper()
        
        if editor_input == 'Y':
            # Check if movies folder exists
            movies_path, directories = get_movies_directories()
            if movies_path and directories:
                use_editor_selection = True
            else:
                print("  ❌ No video editor folders found!")
                if not movies_path:
                    print("  Movies folder not found in any known location.")
                else:
                    print(f"  No subdirectories in: {movies_path}")
                print("  Falling back to manual input...\n")
                use_editor_selection = False
        elif editor_input != 'N':
            print("  Invalid input, using manual input...\n")
        
        # Get user inputs
        video_path, num_exports, start_num, enable_pitch, text_size, enable_color_mode, preset, watermark_size = get_user_inputs(use_editor_selection)
        
        initial_info = get_video_info(video_path)
        initial_size = initial_info['size'] / (1024 * 1024)
        initial_duration = initial_info['duration']
        original_fps = initial_info.get('fps', 30.0)
        
        target_volume_db = get_audio_volume(video_path) if initial_info.get('has_audio') else -20.0
        
        print(f"\nConfiguration:")
        print(f"  Video: {video_path}")
        print(f"  Codec: {initial_info.get('video_codec', 'unknown')}")
        print(f"  Size: {initial_size:.2f} MB")
        print(f"  Duration: {initial_duration:.2f}s")
        print(f"  Frame Rate: {original_fps:.2f} fps (locked)")
        print(f"  Resolution: {initial_info.get('width', 0)}x{initial_info.get('height', 0)}")
        print(f"  Has Audio: {initial_info.get('has_audio', False)}")
        print(f"  Target Volume: {target_volume_db:.1f}dB")
        print(f"  Exports: {num_exports}")
        print(f"  Starting Number: {start_num}")
        print(f"  Pitch Increase: {'YES' if enable_pitch else 'NO'}")
        print(f"  Text Size: {text_size}")
        print(f"  Color Mode: {'YES (hue +25)' if enable_color_mode else 'NO'}")
        print(f"  Preset: {preset}")
        print(f"  Mode: {'MoviePy' if use_moviepy else 'FFmpeg'}")
        if enable_pitch and not use_moviepy:
            print(f"    Filter: rubberband=tempo=2.0:pitch={FIXED_PITCH_RATIO}")
            print(f"    Applied to each export (compounds naturally)")
        elif enable_pitch and use_moviepy:
            print(f"    Note: MoviePy speedup will raise pitch naturally")
        elif use_moviepy:
            print(f"    Note: Audio pitch preserved using ffmpeg atempo/rubberband")
        if not use_moviepy:
            print(f"  Rubberband: {'Available' if has_rubberband else 'NOT available (fallback)'}")
        print(f"  Watermark Size: {watermark_size} (75% opacity)")
        print(f"  Speed Correction: Auto (target {TARGET_SPEED_RATIO}x, tolerance {SPEED_RATIO_TOLERANCE*100:.1f}%)")
        print(f"  Processing: CUMULATIVE")
        
        exported_files = []
        
        print(f"\nStarting export process...")
        print(f"Flow: Original → Export 1 → Export 2 → ... → Export {num_exports}")
        if enable_pitch and not use_moviepy:
            print(f"Pitch: rubberband=tempo=2.0:pitch={FIXED_PITCH_RATIO} each export")
        print()
        
        current_input = video_path
        reference_size = initial_size
        
        if use_moviepy:
            # MoviePy mode with progress bar
            print(f"{'='*60}")
            print("PROCESSING WITH MOVIEPY")
            print(f"{'='*60}")
            init_progress_bar()
            
            for i in range(num_exports):
                export_num = start_num + i
                
                base_name = f"export-{export_num}"
                output_path, actual_name = get_unique_filename(exports_dir, base_name)
                
                try:
                    success = process_video_cumulative(
                        current_input,
                        output_path,
                        export_num,
                        i,
                        reference_size,
                        enable_pitch,
                        has_rubberband,
                        has_loudnorm,
                        target_volume_db,
                        original_fps,
                        use_moviepy=True,
                        silent=True,
                        text_size=text_size,
                        enable_color_mode=enable_color_mode,
                        preset=preset
                    )
                    
                    if not success:
                        raise RuntimeError(f"Failed export {export_num}")
                    
                    exported_files.append(output_path)
                    current_input = output_path
                    
                    # Update progress bar
                    print_progress_bar(i + 1, num_exports)
                    
                except Exception as e:
                    finish_progress_bar()
                    raise RuntimeError(f"Export {export_num} failed: {e}")
            
            finish_progress_bar()
            
        else:
            # FFmpeg mode with detailed output
            for i in range(num_exports):
                export_num = start_num + i
                
                if i >= num_exports:
                    print(f"\n✓ Export requirement met")
                    break
                
                current_pow = 2 ** export_num
                power_display = format_power_notation(current_pow)
                
                base_name = f"export-{export_num}"
                output_path, actual_name = get_unique_filename(exports_dir, base_name)
                
                if enable_pitch:
                    cumulative_semitones = (i + 1) * 1
                    pitch_info = f"+{cumulative_semitones} semitones total"
                else:
                    pitch_info = "None"
                
                print(f"\n{'='*60}")
                print(f"[Export {i+1}/{num_exports}]")
                print(f"  Export Number: {export_num}")
                print(f"  Input: {os.path.basename(current_input)}")
                print(f"  Output: {actual_name}.mp4")
                print(f"  Text: '{export_num} - {power_display}'")
                print(f"  Pitch: {pitch_info}")
                print(f"  Expected Speed: {2**(i+1)}x from original")
                if enable_color_mode:
                    print(f"  Color: Hue +25")
                print(f"{'='*60}")
                
                success = process_video_cumulative(
                    current_input,
                    output_path,
                    export_num,
                    i,
                    reference_size,
                    enable_pitch,
                    has_rubberband,
                    has_loudnorm,
                    target_volume_db,
                    original_fps,
                    use_moviepy=False,
                    silent=False,
                    text_size=text_size,
                    enable_color_mode=enable_color_mode,
                    preset=preset
                )
                
                if not success:
                    raise RuntimeError(f"Failed export {export_num}")
                
                exported_files.append(output_path)
                
                output_size = check_file_size(output_path)
                speedup = 2 ** (i + 1)
                size_percent = (output_size / initial_size) * 100
                
                print(f"  Size: {output_size:.2f} MB ({size_percent:.1f}%)")
                
                current_input = output_path
        
        print(f"\n{'='*60}")
        print(f"✓ ALL {num_exports} EXPORTS COMPLETED!")
        print(f"{'='*60}")
        print(f"Location: {os.path.abspath(exports_dir)}")
        print(f"\nExport Summary:")
        print(f"{'='*60}")
        
        for i, export_file in enumerate(exported_files):
            export_num = start_num + i
            size = check_file_size(export_file)
            info = get_video_info(export_file)
            pow_val = 2 ** export_num
            pow_display = format_power_notation(pow_val)
            speedup = 2 ** (i + 1)
            size_ratio = (size / initial_size) * 100
            
            if enable_pitch:
                cumulative_semitones = (i + 1) * 1
                pitch_display = f"+{cumulative_semitones}st"
            else:
                pitch_display = "None"
            
            print(f"\n  {os.path.basename(export_file)}:")
            print(f"    Size: {size:.2f} MB ({size_ratio:.1f}%)")
            print(f"    Duration: {info['duration']:.2f}s")
            print(f"    Text: '{export_num} - {pow_display}'")
            print(f"    Speed: {speedup}x | Pitch: {pitch_display}")
            if enable_color_mode:
                print(f"    Color: Hue +25")
        
        print(f"\n{'='*60}")
        
        compile_input = input("\nCompile all exports into Video? (N/Y): ").strip().upper()
        
        if compile_input == 'Y':
            compile_exports(exported_files, exports_dir, original_fps, preset, watermark_size)
        else:
            print("Skipping compilation.")
        
        print(f"\n{'='*60}")
        print("✓ ALL DONE!")
        print(f"{'='*60}")
        
    except KeyboardInterrupt:
        print("\n\n⚠ Interrupted by user")
        sys.exit(1)
    except FileNotFoundError as e:
        print(f"\n❌ File Error: {e}")
        sys.exit(1)
    except ValueError as e:
        print(f"\n❌ Input Error: {e}")
        sys.exit(1)
    except SystemError as e:
        print(f"\n❌ System Error: {e}")
        sys.exit(1)
    except RuntimeError as e:
        print(f"\n❌ Processing Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
