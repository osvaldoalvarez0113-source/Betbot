import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ODDS_API_KEY",        "")
os.environ.setdefault("ANTHROPIC_API_KEY",   "")
os.environ.setdefault("GITHUB_TOKEN",        "")
os.environ.setdefault("OPENWEATHER_API_KEY", "")
