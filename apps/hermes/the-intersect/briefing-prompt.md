Generate the owner's morning briefing for Lake Forest Park / Seattle. Keep it tight, scannable, and friendly: plain text suitable for Telegram, with no markdown tables. Use these live sources and DO NOT fabricate anything. If a source fails, say so briefly and continue.

1) WEATHER (lfpweather MCP): Call its query_weather tool for metric "temperature" over range "24h" with aggregations ["max","min"] to get the overnight low and recent high. Also call its get_weather_latest tool with metrics ["temperature","aqi","co2","wind_speed","rain_24h"] for current conditions. Report the overnight low, today's high so far and current temperature, wind, and any rain.

2) AIR QUALITY (lfpweather): From the same latest call, report AQI (with the Good/Moderate label) and CO2 ppm.

3) BIRDS (lfpweather): Call its get_bird_detections tool (default 24h range). Highlight 3-5 notable or interesting species (owls, raptors, water birds, or anything uncommon), not just the top counts.

4) NEWS: Fetch these Google News RSS feeds and extract 3 real, dated headlines from each (title and source, skipping homepages):
   - Local: https://news.google.com/rss/search?q=Seattle+when:1d&hl=en-US&gl=US&ceid=US:en
   - National/World: https://news.google.com/rss/headlines/section/topic/NATION?hl=en-US&gl=US&ceid=US:en
   Give about 3 local Seattle/PNW headlines and about 3 national/world headlines.

5) WINDOWS CALL: One line. Based on temperature, air quality, and rain, say whether it's a good morning to open the windows.

Start with a one-line greeting and the date. End with nothing extra. If the lfpweather tools aren't callable, note it and still deliver the news section.
