import requests

VIDEO_URL = open("urls.txt", "r", encoding="utf-8").read().strip()

def fetch_video_url(video_url):
    api = f"https://www.tikwm.com/api/?url={video_url}"

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    res = requests.get(api, headers=headers)
    res.raise_for_status()

    data = res.json().get("data", {})

    return data.get("play")

def download_file(url, output):
    response = requests.get(url, stream=True)
    response.raise_for_status()

    with open(output, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)

print("Fetching TikTok download URL...")

play_url = fetch_video_url(VIDEO_URL)

if not play_url:
    raise Exception("Failed get video URL")

print("Downloading video...")

download_file(play_url, "input_video.mp4")

print("Saved as input_video.mp4")
