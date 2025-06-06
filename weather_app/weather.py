import requests
import sys

API_URL = 'https://wttr.in/{}?format=3'


def get_weather(location: str) -> str:
    """Fetch simple weather information for a location."""
    response = requests.get(API_URL.format(location))
    response.raise_for_status()
    return response.text.strip()


def main(argv=None):
    argv = argv or sys.argv[1:]
    if not argv:
        print("Usage: python weather.py <location>")
        return
    location = ' '.join(argv)
    try:
        weather = get_weather(location)
        print(weather)
    except requests.RequestException as exc:
        print(f"Error fetching weather: {exc}")


if __name__ == '__main__':
    main()
