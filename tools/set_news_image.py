"""Put a PNG or JPG into a news post: python tools/set_news_image.py <post Id> <image file> ["attribution"]
   python tools/set_news_image.py 1 none        removes the image.
The game loads it with Unity's Texture2D.LoadImage (PNG/JPG). Keep it small (it travels as base64 in the JSON):
a JPG around 1024x512 and under ~300 KB is plenty. Bumps the post's Version so games fetch it again."""
import base64
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NEWS = os.path.join(HERE, "..", "content", "news.json")

if len(sys.argv) < 3:
    sys.exit(__doc__)
post_id, image = int(sys.argv[1]), sys.argv[2]
with open(NEWS, encoding="utf-8") as f:
    news = json.load(f)
post = next((n for n in news if n.get("Id") == post_id), None)
if post is None:
    sys.exit(f"no post with Id {post_id} in content/news.json")
if image.lower() == "none":
    post["ImageData"] = None
else:
    data = open(image, "rb").read()
    if not (data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff"):
        sys.exit("not a PNG or JPG")
    if len(data) > 1_000_000:
        print(f"warning: {len(data) // 1024} KB is big for a news image; every player downloads it")
    post["ImageData"] = base64.b64encode(data).decode("ascii")
    if len(sys.argv) > 3:
        post["ImageAttribution"] = sys.argv[3]
post["Version"] = int(post.get("Version", 1)) + 1
with open(NEWS, "w", encoding="utf-8") as f:
    json.dump(news, f, indent=2)
print(f"post {post_id}: image {'removed' if post['ImageData'] is None else 'set'}, Version {post['Version']}")
