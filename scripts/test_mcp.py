from fastmcp import FastMCP
import requests
mcp=FastMCP(
    name="weather_mcp",
    instructions='天气查询服务，可实时查询天气'
)

@mcp.tool
def get_weather():
    geocoding_url = "https://geocoding-api.open-meteo.com/v1/search"
    open_meteo_url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "name":"shanghai",
        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m",
    }
    response = requests.get(geocoding_url, params=params)
    latitude=response.json()["results"][0]["latitude"]
    longitude=response.json()["results"][0]["longitude"]


    params = {
        "latitude":latitude,
        "longitude":longitude,
        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m",
    }
    response = requests.get(open_meteo_url, params=params)
    print(response.json())
if __name__ == "__main__":
    mcp.run()

