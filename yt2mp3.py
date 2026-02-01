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
        'ignoreerrors': True,                                   # prevent crashing if video was taken down or anything
        'download_archive': f'{output_path}/.downloaded.txt',   # download log to skip duplicates
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'referer': 'https://www.youtube.com/',
        'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
        'nocheckcertificate': False,
        'quiet': False,
        'no_warnings': False,
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