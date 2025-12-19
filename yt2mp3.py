import sys
import yt_dlp

def download_youtube_audio(url, output_path='downloads'):
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '320',
        }],
        'outtmpl': f'{output_path}/%(title)s.%(ext)s',
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

if __name__ == '__main__':
    if len(sys.argv) >= 2:
        url = sys.argv[1]
    else:
        url = input('Enter YouTube URL: ').strip()
        if not url:
            print('No URL provided')
            sys.exit(1)

    print(url)
    download_youtube_audio(url)