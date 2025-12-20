import os
import sys
import subprocess
import shutil
from pathlib import Path
import math
import json

# Fixed pitch ratio: 2^(1/12) = 1 semitone up
FIXED_PITCH_RATIO = 1.059463094352953

# Output FPS
OUTPUT_FPS = 60

# Expected speed ratio
EXPECTED_SPEED = 2.0
SPEED_TOLERANCE = 0.02  # 2% tolerance

# ANSI color codes
class Colors:
    ORANGE = '\033[38;5;208m'
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

def print_welcome():
    """Print welcome message and instructions"""
    print(f"\n{Colors.ORANGE}{Colors.BOLD}{'='*60}")
    print(f"  Welcome to SpeedExp (SpeedyCollab but mobile)")
    print(f"{'='*60}{Colors.RESET}\n")
    
    print(f"{Colors.CYAN}How to use:{Colors.RESET}")
    print(f"  1. Enter path to your video file")
    print(f"  2. Enter how many exports you want")
    print(f"  3. Enter starting number for export names")
    print(f"  4. Choose if you want pitch increase (Y/N)")
    print()
    print(f"{Colors.CYAN}What it does:{Colors.RESET}")
    print(f"  • Speeds up video 2x each export")
    print(f"  • Duplicates to keep same duration")
    print(f"  • Adds text overlay showing export number")
    print(f"  • Optionally increases pitch each export")
    print(f"  • Can compile all exports into one video")
    print()
    print(f"{Colors.CYAN}Output:{Colors.RESET}")
    print(f"  • Exports saved to: Downloads/Exports/")
    print(f"  • Format: export-N.mp4")
    print(f"  • FPS: {OUTPUT_FPS}")
    print()
    print(f"{Colors.ORANGE}{'='*60}{Colors.RESET}\n")

def check_dependencies():
    """Check if required dependencies are installed"""
    if not shutil.which('ffmpeg'):
        raise SystemError("ffmpeg is not installed or not in PATH.")
    
    if not shutil.which('ffprobe'):
        raise SystemError("ffprobe is not installed or not in PATH.")
    
    print(f"{Colors.GREEN}✓{Colors.RESET} FFmpeg found")
    print(f"{Colors.GREEN}✓{Colors.RESET} FFprobe found")
    
    result = subprocess.run(['ffmpeg', '-filters'], capture_output=True, text=True)
    has_rubberband = 'rubberband' in result.stdout
    has_loudnorm = 'loudnorm' in result.stdout
    
    if has_rubberband:
        print(f"{Colors.GREEN}✓{Colors.RESET} Rubberband filter available")
    else:
        print(f"{Colors.YELLOW}⚠{Colors.RESET} Rubberband filter NOT available (will use atempo fallback)")
    
    if has_loudnorm:
        print(f"{Colors.GREEN}✓{Colors.RESET} Loudnorm filter available")
    else:
        print(f"{Colors.YELLOW}⚠{Colors.RESET} Loudnorm filter NOT available")
    
    return has_rubberband, has_loudnorm

def get_ffmpeg_version():
    """Get ffmpeg version"""
    try:
        result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True)
        return result.stdout.split('\n')[0]
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
        }
        
        print(f"  Available codecs: {[k for k, v in available.items() if v]}")
        return available
        
    except:
        return {'libx264': True, 'mpeg4': True}

def select_codec_configs():
    """Select codec configurations to try"""
    available = get_available_codecs()
    
    configs = []
    
    if available.get('libx264') or available.get('h264'):
        configs.append({
            'name': 'H.264 Baseline',
            'codec': 'libx264',
            'params': ['-profile:v', 'baseline', '-level', '3.0', '-pix_fmt', 'yuv420p', '-preset', 'fast']
        })
        configs.append({
            'name': 'H.264 Main',
            'codec': 'libx264',
            'params': ['-profile:v', 'main', '-pix_fmt', 'yuv420p', '-preset', 'fast']
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
    """Get video information"""
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
        
        return {
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
                    return float(parts[1].strip().split(' ')[0])
        
        return -20.0
        
    except:
        return -20.0

def format_power_notation(number):
    """Format large numbers in scientific notation"""
    if number < 1_000_000:
        return str(number)
    else:
        exponent = math.floor(math.log10(number))
        mantissa = number / (10 ** exponent)
        return f"{mantissa:.2f} * 10^{exponent}"

def get_user_inputs():
    """Get and validate user inputs"""
    try:
        video_path = input(f"{Colors.CYAN}Video File Location?:{Colors.RESET} ").strip()
        if not video_path:
            raise ValueError("Video file location cannot be empty")
        
        video_path = video_path.strip('"').strip("'")
        validate_video_file(video_path)
        print(f"  {Colors.GREEN}✓{Colors.RESET} Video validated")
        
        exports_str = input(f"{Colors.CYAN}How much exports?:{Colors.RESET} ").strip()
        if not exports_str.isdigit():
            raise ValueError("Number of exports must be a positive integer")
        num_exports = int(exports_str)
        if num_exports <= 0:
            raise ValueError("Number of exports must be greater than 0")
        if num_exports > 20:
            confirm = input(f"{Colors.YELLOW}Warning: {num_exports} exports may take long. Continue? (y/n):{Colors.RESET} ")
            if confirm.lower() != 'y':
                raise ValueError("Export cancelled")
        
        start_str = input(f"{Colors.CYAN}Starting Number?:{Colors.RESET} ").strip()
        if not start_str.isdigit():
            raise ValueError("Starting number must be a positive integer")
        start_num = int(start_str)
        if start_num < 0:
            raise ValueError("Starting number must be non-negative")
        
        pitch_input = input(f"{Colors.CYAN}Set Pitch Increase (N/Y)?:{Colors.RESET} ").strip().upper()
        if pitch_input not in ['N', 'Y']:
            print(f"  {Colors.YELLOW}Invalid input, defaulting to N{Colors.RESET}")
            enable_pitch = False
        else:
            enable_pitch = pitch_input == 'Y'
        
        return video_path, num_exports, start_num, enable_pitch
        
    except Exception as e:
        raise e

def create_exports_folder():
    """Create Exports folder"""
    termux_downloads = "/data/data/com.termux/files/home/storage/downloads"
    
    if os.path.exists(termux_downloads):
        exports_dir = Path(termux_downloads) / "Exports"
    else:
        print(f"{Colors.YELLOW}Warning: Termux not detected. Using current directory.{Colors.RESET}")
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

def verify_output_file(file_path, min_size_kb=70):
    """Verify output file is valid"""
    if not os.path.exists(file_path):
        return False, "File not created"
    
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

def correct_speed(input_path, output_path, correction_factor, has_rubberband, has_audio):
    """Apply speed correction to fix misaligned video"""
    try:
        # Video: setpts = 1/correction_factor * PTS
        pts_factor = 1.0 / correction_factor
        
        if has_audio:
            if has_rubberband:
                audio_filter = f"rubberband=tempo={correction_factor}"
            else:
                # atempo only supports 0.5 to 2.0, chain if needed
                if correction_factor <= 2.0:
                    audio_filter = f"atempo={correction_factor}"
                else:
                    audio_filter = f"atempo=2.0,atempo={correction_factor/2.0}"
            
            cmd = [
                'ffmpeg', '-i', input_path,
                '-vf', f'setpts={pts_factor}*PTS',
                '-af', audio_filter,
                '-c:v', 'libx264',
                '-preset', 'fast',
                '-crf', '23',
                '-pix_fmt', 'yuv420p',
                '-r', str(OUTPUT_FPS),
                '-c:a', 'aac',
                '-b:a', '128k',
                '-ar', '44100',
                '-y', output_path
            ]
        else:
            cmd = [
                'ffmpeg', '-i', input_path,
                '-vf', f'setpts={pts_factor}*PTS',
                '-c:v', 'libx264',
                '-preset', 'fast',
                '-crf', '23',
                '-pix_fmt', 'yuv420p',
                '-r', str(OUTPUT_FPS),
                '-an',
                '-y', output_path
            ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.returncode == 0
        
    except:
        return False

def process_video_cumulative(input_path, output_path, export_num, iteration, reference_size_mb, 
                             enable_pitch, has_rubberband, has_loudnorm, target_volume_db):
    """Process video cumulatively with speed correction"""
    
    temp_files = []
    
    try:
        export_pow = 2 ** export_num
        power_text = format_power_notation(export_pow)
        
        print(f"  Export power (2^{export_num}): {power_text}")
        
        text_string = f"{export_num} - {power_text}"
        text_escaped = escape_text_for_ffmpeg(text_string)
        
        input_info = get_video_info(input_path)
        input_duration = input_info['duration']
        input_size_mb = input_info['size'] / (1024 * 1024)
        has_audio = input_info['has_audio']
        
        expected_sped_duration = input_duration / EXPECTED_SPEED
        
        print(f"  Input: {input_duration:.2f}s, {input_size_mb:.2f} MB")
        print(f"  Expected after 2x: {expected_sped_duration:.2f}s")
        print(f"  Output FPS: {OUTPUT_FPS}")
        
        # Volume adjustment
        current_volume = get_audio_volume(input_path) if has_audio else -20.0
        volume_adjustment = target_volume_db - current_volume
        
        if enable_pitch:
            cumulative_semitones = (iteration + 1) * 1
            print(f"  Pitch: +{cumulative_semitones} semitones (cumulative)")
        print(f"  Volume: {current_volume:.1f}dB -> {target_volume_db:.1f}dB")
        
        # Temp file paths
        temp_dir = os.path.dirname(output_path)
        temp_sped = os.path.join(temp_dir, f"temp_sped_{export_num}_{os.getpid()}.mp4")
        temp_corrected = os.path.join(temp_dir, f"temp_corrected_{export_num}_{os.getpid()}.mp4")
        temp_list = os.path.join(temp_dir, f"temp_list_{export_num}_{os.getpid()}.txt")
        temp_concat = os.path.join(temp_dir, f"temp_concat_{export_num}_{os.getpid()}.mp4")
        
        temp_files = [temp_sped, temp_corrected, temp_list, temp_concat]
        
        # Step 1: Speed up using rubberband tempo
        if has_audio:
            if has_rubberband:
                if enable_pitch:
                    print(f"  Step 1/4: rubberband tempo=2.0 pitch={FIXED_PITCH_RATIO}...")
                    audio_filter = f"rubberband=tempo=2.0:pitch={FIXED_PITCH_RATIO},volume={volume_adjustment}dB"
                else:
                    print(f"  Step 1/4: rubberband tempo=2.0...")
                    audio_filter = f"rubberband=tempo=2.0,volume={volume_adjustment}dB"
            else:
                if enable_pitch:
                    print(f"  Step 1/4: atempo=2.0 + asetrate (fallback)...")
                    pitched_rate = int(44100 * FIXED_PITCH_RATIO)
                    audio_filter = f"atempo=2.0,asetrate={pitched_rate},aresample=44100,volume={volume_adjustment}dB"
                else:
                    print(f"  Step 1/4: atempo=2.0 (fallback)...")
                    audio_filter = f"atempo=2.0,volume={volume_adjustment}dB"
            
            cmd_speed = [
                'ffmpeg', '-i', input_path,
                '-vf', 'setpts=0.5*PTS',
                '-af', audio_filter,
                '-c:v', 'libx264',
                '-preset', 'fast',
                '-crf', '23',
                '-pix_fmt', 'yuv420p',
                '-r', str(OUTPUT_FPS),
                '-c:a', 'aac',
                '-b:a', '128k',
                '-ar', '44100',
                '-y', temp_sped
            ]
        else:
            print(f"  Step 1/4: setpts=0.5*PTS (no audio)...")
            cmd_speed = [
                'ffmpeg', '-i', input_path,
                '-vf', 'setpts=0.5*PTS',
                '-c:v', 'libx264',
                '-preset', 'fast',
                '-crf', '23',
                '-pix_fmt', 'yuv420p',
                '-r', str(OUTPUT_FPS),
                '-an',
                '-y', temp_sped
            ]
        
        result = subprocess.run(cmd_speed, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Speed-up failed: {result.stderr[-300:]}")
        
        valid, msg = verify_output_file(temp_sped)
        if not valid:
            raise RuntimeError(f"Speed-up invalid: {msg}")
        
        sped_info = get_video_info(temp_sped)
        actual_sped_duration = sped_info['duration']
        
        speed_ratio = input_duration / actual_sped_duration if actual_sped_duration > 0 else 0
        print(f"    Result: {actual_sped_duration:.2f}s (ratio: {speed_ratio:.2f}x)")
        
        # Step 2: Speed correction if needed
        current_file = temp_sped
        speed_diff = abs(speed_ratio - EXPECTED_SPEED)
        
        if speed_diff > SPEED_TOLERANCE:
            # Calculate correction factor
            correction_factor = EXPECTED_SPEED / speed_ratio
            print(f"  Step 2/4: Speed correction (factor: {correction_factor:.4f})...")
            
            correction_success = correct_speed(
                temp_sped, 
                temp_corrected, 
                correction_factor, 
                has_rubberband, 
                has_audio
            )
            
            if correction_success:
                valid, msg = verify_output_file(temp_corrected)
                if valid:
                    corrected_info = get_video_info(temp_corrected)
                    corrected_duration = corrected_info['duration']
                    new_ratio = input_duration / corrected_duration if corrected_duration > 0 else 0
                    print(f"    Corrected: {corrected_duration:.2f}s (ratio: {new_ratio:.2f}x)")
                    current_file = temp_corrected
                else:
                    print(f"    {Colors.YELLOW}⚠ Correction failed, using original{Colors.RESET}")
            else:
                print(f"    {Colors.YELLOW}⚠ Correction failed, using original{Colors.RESET}")
        else:
            print(f"  Step 2/4: Speed OK (within {SPEED_TOLERANCE*100:.0f}% tolerance)")
        
        # Step 3: Duplicate
        print(f"  Step 3/4: Duplicating...")
        
        abs_current = os.path.abspath(current_file)
        with open(temp_list, 'w') as f:
            f.write(f"file '{abs_current}'\n")
            f.write(f"file '{abs_current}'\n")
        
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
        print(f"    Result: {concat_duration:.2f}s")
        
        # Step 4: Add text overlay
        print(f"  Step 4/4: Adding text...")
        
        target_size_mb = reference_size_mb * 1.15
        target_bitrate_total = (target_size_mb * 8 * 1024) / concat_duration
        audio_bitrate = 128
        target_video_bitrate = max(500, int(target_bitrate_total - audio_bitrate))
        
        drawtext_filter = (
            f"drawtext=text='{text_escaped}':"
            f"fontcolor=red:"
            f"bordercolor=blue:borderw=3:"
            f"fontsize=111:"
            f"box=1:boxcolor=black@0.12:boxborderw=8:"
            f"x=20:y=h-th-20"
        )
        
        codec_configs = select_codec_configs()
        success = False
        last_error = None
        
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
            
            if concat_info.get('has_audio', True):
                cmd_text = [
                    'ffmpeg', '-i', temp_concat,
                    '-vf', drawtext_filter,
                    '-c:v', codec
                ] + codec_params + [
                    '-b:v', f'{target_video_bitrate}k',
                    '-maxrate', f'{int(target_video_bitrate * 1.5)}k',
                    '-bufsize', f'{int(target_video_bitrate * 2)}k',
                    '-r', str(OUTPUT_FPS),
                    '-c:a', 'aac',
                    '-b:a', '128k',
                    '-movflags', '+faststart',
                    '-y', output_path
                ]
            else:
                cmd_text = [
                    'ffmpeg', '-i', temp_concat,
                    '-vf', drawtext_filter,
                    '-c:v', codec
                ] + codec_params + [
                    '-b:v', f'{target_video_bitrate}k',
                    '-maxrate', f'{int(target_video_bitrate * 1.5)}k',
                    '-bufsize', f'{int(target_video_bitrate * 2)}k',
                    '-r', str(OUTPUT_FPS),
                    '-an',
                    '-movflags', '+faststart',
                    '-y', output_path
                ]
            
            result = subprocess.run(cmd_text, capture_output=True, text=True)
            
            if result.returncode == 0:
                valid, msg = verify_output_file(output_path, min_size_kb=70)
                if valid:
                    output_info = get_video_info(output_path)
                    output_size_mb = output_info['size'] / (1024 * 1024)
                    print(f"    {Colors.GREEN}✓{Colors.RESET} {codec_name}: {output_size_mb:.2f} MB")
                    success = True
                else:
                    print(f"    {Colors.RED}✗{Colors.RESET} {codec_name}: {msg}")
                    last_error = msg
            else:
                error_msg = result.stderr[-200:] if result.stderr else "Unknown"
                print(f"    {Colors.RED}✗{Colors.RESET} {codec_name}: {error_msg}")
                last_error = error_msg
        
        if not success:
            raise RuntimeError(f"All codecs failed: {last_error}")
        
        final_info = get_video_info(output_path)
        final_duration = final_info['duration']
        cumulative_speed = 2 ** (iteration + 1)
        
        print(f"{Colors.GREEN}✓{Colors.RESET} Export {export_num} completed")
        print(f"    Duration: {final_duration:.2f}s | Speed: {cumulative_speed}x | FPS: {OUTPUT_FPS}")
        
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

def compile_exports(export_files, exports_dir):
    """Compile all exports into single video with watermark"""
    try:
        print(f"\n{Colors.ORANGE}{'='*60}")
        print("COMPILING ALL EXPORTS...")
        print(f"{'='*60}{Colors.RESET}")
        
        output_path, output_name = get_unique_filename(exports_dir, "SpeedExp-Compilation")
        
        print(f"  Output: {output_name}.mp4")
        print(f"  Merging {len(export_files)} exports...")
        
        temp_list = os.path.join(exports_dir, f"temp_compile_list_{os.getpid()}.txt")
        temp_concat = os.path.join(exports_dir, f"temp_compile_concat_{os.getpid()}.mp4")
        
        with open(temp_list, 'w') as f:
            for export_file in export_files:
                abs_path = os.path.abspath(export_file)
                f.write(f"file '{abs_path}'\n")
        
        print(f"  Step 1/2: Concatenating...")
        
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
        print(f"    Duration: {concat_info['duration']:.2f}s")
        
        print(f"  Step 2/2: Adding watermark...")
        
        watermark_text = escape_text_for_ffmpeg("Made with SpeedExp.py.")
        
        watermark_filter = (
            f"drawtext=text='{watermark_text}':"
            f"fontcolor=white@0.75:"
            f"bordercolor=black@0.75:borderw=2:"
            f"fontsize=60:"
            f"box=1:boxcolor=orange@0.75:boxborderw=5:"
            f"x=w-tw-20:y=20"
        )
        
        codec_configs = select_codec_configs()
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
                '-r', str(OUTPUT_FPS),
                '-c:a', 'aac',
                '-b:a', '128k',
                '-movflags', '+faststart',
                '-y', output_path
            ]
            
            result = subprocess.run(cmd_watermark, capture_output=True, text=True)
            
            if result.returncode == 0:
                valid, msg = verify_output_file(output_path, min_size_kb=70)
                if valid:
                    print(f"    {Colors.GREEN}✓{Colors.RESET} {codec_name}")
                    success = True
                else:
                    print(f"    {Colors.RED}✗{Colors.RESET} {codec_name}: {msg}")
            else:
                error_msg = result.stderr[-200:] if result.stderr else "Unknown"
                print(f"    {Colors.RED}✗{Colors.RESET} {codec_name}: {error_msg}")
        
        for temp_file in [temp_list, temp_concat]:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except:
                    pass
        
        if not success:
            raise RuntimeError("Failed to add watermark")
        
        final_info = get_video_info(output_path)
        final_size = final_info['size'] / (1024 * 1024)
        
        print(f"\n{Colors.GREEN}✓ COMPILATION COMPLETE!{Colors.RESET}")
        print(f"  File: {output_name}.mp4")
        print(f"  Size: {final_size:.2f} MB")
        print(f"  Duration: {final_info['duration']:.2f}s")
        print(f"  FPS: {OUTPUT_FPS}")
        
        return output_path
        
    except Exception as e:
        raise RuntimeError(f"Compilation failed: {str(e)}")

def check_file_size(file_path):
    """Get file size in MB"""
    if os.path.exists(file_path):
        return os.path.getsize(file_path) / (1024 * 1024)
    return 0

def main():
    """Main function"""
    try:
        print_welcome()
        
        print("Checking dependencies...")
        has_rubberband, has_loudnorm = check_dependencies()
        
        ffmpeg_version = get_ffmpeg_version()
        print(f"  FFmpeg: {ffmpeg_version}")
        print()
        
        termux_path = "/data/data/com.termux/files/home/storage/downloads"
        if os.path.exists(termux_path):
            print(f"{Colors.GREEN}✓{Colors.RESET} Termux detected")
            print(f"  Output: {termux_path}/Exports\n")
        else:
            print(f"{Colors.YELLOW}⚠{Colors.RESET} Using current directory\n")
        
        video_path, num_exports, start_num, enable_pitch = get_user_inputs()
        
        initial_info = get_video_info(video_path)
        initial_size = initial_info['size'] / (1024 * 1024)
        initial_duration = initial_info['duration']
        
        target_volume_db = get_audio_volume(video_path) if initial_info.get('has_audio') else -20.0
        
        print(f"\n{Colors.CYAN}Configuration:{Colors.RESET}")
        print(f"  Video: {video_path}")
        print(f"  Size: {initial_size:.2f} MB")
        print(f"  Duration: {initial_duration:.2f}s")
        print(f"  Input FPS: {initial_info.get('fps', 30):.2f}")
        print(f"  Output FPS: {OUTPUT_FPS}")
        print(f"  Resolution: {initial_info.get('width', 0)}x{initial_info.get('height', 0)}")
        print(f"  Has Audio: {initial_info.get('has_audio', False)}")
        print(f"  Exports: {num_exports}")
        print(f"  Starting Number: {start_num}")
        print(f"  Pitch: {'YES' if enable_pitch else 'NO'}")
        print(f"  Audio Method: {'rubberband' if has_rubberband else 'atempo (fallback)'}")
        print(f"  Speed Correction: Enabled (tolerance: {SPEED_TOLERANCE*100:.0f}%)")
        if has_rubberband:
            if enable_pitch:
                print(f"    Filter: rubberband=tempo=2.0:pitch={FIXED_PITCH_RATIO}")
            else:
                print(f"    Filter: rubberband=tempo=2.0")
        
        exports_dir = create_exports_folder()
        exported_files = []
        
        print(f"\n{Colors.ORANGE}Starting exports...{Colors.RESET}")
        print(f"Flow: Original → Export 1 → Export 2 → ... → Export {num_exports}\n")
        
        current_input = video_path
        reference_size = initial_size
        
        for i in range(num_exports):
            export_num = start_num + i
            
            current_pow = 2 ** export_num
            power_display = format_power_notation(current_pow)
            
            base_name = f"export-{export_num}"
            output_path, actual_name = get_unique_filename(exports_dir, base_name)
            
            if enable_pitch:
                pitch_info = f"+{(i+1)} semitones"
            else:
                pitch_info = "None"
            
            print(f"\n{Colors.BLUE}{'='*60}")
            print(f"[Export {i+1}/{num_exports}]")
            print(f"{'='*60}{Colors.RESET}")
            print(f"  Number: {export_num}")
            print(f"  Input: {os.path.basename(current_input)}")
            print(f"  Output: {actual_name}.mp4")
            print(f"  Text: '{export_num} - {power_display}'")
            print(f"  Pitch: {pitch_info}")
            print(f"  Target Speed: {2**(i+1)}x from original")
            
            success = process_video_cumulative(
                current_input,
                output_path,
                export_num,
                i,
                reference_size,
                enable_pitch,
                has_rubberband,
                has_loudnorm,
                target_volume_db
            )
            
            if not success:
                raise RuntimeError(f"Failed export {export_num}")
            
            exported_files.append(output_path)
            
            output_size = check_file_size(output_path)
            size_percent = (output_size / initial_size) * 100
            print(f"  Size: {output_size:.2f} MB ({size_percent:.1f}%)")
            
            current_input = output_path
        
        print(f"\n{Colors.GREEN}{'='*60}")
        print(f"✓ ALL {num_exports} EXPORTS COMPLETED!")
        print(f"{'='*60}{Colors.RESET}")
        print(f"Location: {os.path.abspath(exports_dir)}")
        
        print(f"\n{Colors.CYAN}Summary:{Colors.RESET}")
        for i, export_file in enumerate(exported_files):
            export_num = start_num + i
            size = check_file_size(export_file)
            info = get_video_info(export_file)
            pow_val = 2 ** export_num
            pow_display = format_power_notation(pow_val)
            speedup = 2 ** (i + 1)
            
            if enable_pitch:
                pitch_display = f"+{i+1}st"
            else:
                pitch_display = "-"
            
            print(f"  {os.path.basename(export_file)}: {size:.2f}MB | {info['duration']:.1f}s | {speedup}x | {pitch_display}")
        
        print()
        compile_input = input(f"{Colors.CYAN}Compile all exports into Video? (N/Y):{Colors.RESET} ").strip().upper()
        
        if compile_input == 'Y':
            compile_exports(exported_files, exports_dir)
        else:
            print("Skipping compilation.")
        
        print(f"\n{Colors.GREEN}{'='*60}")
        print("✓ ALL DONE!")
        print(f"{'='*60}{Colors.RESET}")
        
    except KeyboardInterrupt:
        print(f"\n\n{Colors.YELLOW}⚠ Interrupted{Colors.RESET}")
        sys.exit(1)
    except FileNotFoundError as e:
        print(f"\n{Colors.RED}❌ File Error: {e}{Colors.RESET}")
        sys.exit(1)
    except ValueError as e:
        print(f"\n{Colors.RED}❌ Input Error: {e}{Colors.RESET}")
        sys.exit(1)
    except SystemError as e:
        print(f"\n{Colors.RED}❌ System Error: {e}{Colors.RESET}")
        sys.exit(1)
    except RuntimeError as e:
        print(f"\n{Colors.RED}❌ Processing Error: {e}{Colors.RESET}")
        sys.exit(1)
    except Exception as e:
        print(f"\n{Colors.RED}❌ Unexpected Error: {e}{Colors.RESET}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
