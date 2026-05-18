import requests
import os
import time
import random

OUTPUT_DIR = "./downloads"
os.makedirs(OUTPUT_DIR, exist_ok=True)

with open("urls.txt", "r", encoding="utf-8") as f:
    urls = [line.strip() for line in f if line.strip()]

def download_file(url, output):
    response = requests.get(url, stream=True)
    response.raise_for_status()
    with open(output, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)

def fetch_video_url(video_url):
    api = f"https://www.tikwm.com/api/?url={video_url}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(api, headers=headers)
        res.raise_for_status()
        data = res.json().get("data", {})
        return data.get("play")
    except Exception as e:
        print(f"API error for {video_url}: {e}")
        return None

for video_url in urls:
    try:
        print(f"Processing: {video_url}")
        play_url = fetch_video_url(video_url)
        if not play_url:
            print("Failed get video")
            continue
        file_name = f"{int(time.time())}_{random.randint(0,9999)}.mp4"
        output_path = os.path.join(OUTPUT_DIR, file_name)
        download_file(play_url, output_path)
        print(f"Saved: {output_path}")
    except Exception as err:
        print(str(err))
