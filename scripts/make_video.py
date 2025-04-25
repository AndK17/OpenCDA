import argparse
import os
from pathlib import Path
import subprocess
import tempfile

def create_video(input_dir, output_path, framerate=20, rotate=0, pattern="*.png"):
    """
    Creates a video from images.
    
    :param input_dir: Path to directory containing source images
    :param output_path: Output video file path
    :param framerate: Frame rate in frames per second (FPS)
    :param rotate: Rotation angle in degrees counter-clockwise (valid values: 90, 180, 270)
    :param pattern: File pattern for image selection (e.g., *.png, *.jpg)
    """
    try:
        if not os.path.isdir(input_dir):
            raise FileNotFoundError(f"Directory {input_dir} not found")

        # Creates tmp file if rotation needed
        temp_file = None
        if rotate:
            temp_file = Path(tempfile.mktemp(suffix=".mp4"))

        intermediate_path = temp_file if rotate else output_path

        cmd = [
            'ffmpeg',
            '-y',
            '-framerate', str(framerate),
            '-pattern_type', 'glob',
            '-i', str(Path(input_dir) / pattern),
            '-c:v', 'libx264',
            '-pix_fmt', 'yuv420p',
            str(intermediate_path)
        ]

        subprocess.run(cmd, check=True, stderr=subprocess.PIPE)

        if rotate:
            match rotate:
                case 90:
                    rotate_str = 'transpose=2'
                case 180:
                    rotate_str = 'transpose=1,transpose=1'
                case 270:
                    rotate_str = 'transpose=1'
                case _:
                    print("Invalid rotate angle")
                    return -1

            rotate_cmd = [
                'ffmpeg',
                '-y',
                '-i', str(intermediate_path),
                '-vf', rotate_str,
                '-c:a', 'copy',
                str(output_path)
            ]
            subprocess.run(rotate_cmd, check=True, stderr=subprocess.PIPE)
            os.remove(intermediate_path)

        print(f"Video successfully created: {output_path}")

    except subprocess.CalledProcessError as e:
        print(f"FFmpeg error: {e.output}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Create video from frames using ffmpeg-python')
    parser.add_argument('input_dir', help='Directory containing input images')
    parser.add_argument('output_path', default='video.mp4', help='Output video file path (e.g., video.mp4)')
    parser.add_argument('--framerate', type=int, default=20, help='Frame rate in frames per second (FPS)')
    parser.add_argument('--rotate', type=int, default=0, 
                        help='Rotation angle in degrees counter-clockwise (valid values: 90, 180, 270)')
    parser.add_argument('--pattern', default='*.png', 
                        help='Filename pattern for image sequence (glob format)')
    
    args = parser.parse_args()
    create_video(**vars(args))