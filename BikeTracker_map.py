import streamlit as st
import pandas as pd
import h3
import pydeck as pdk
from datetime import datetime
from geopy.geocoders import Nominatim

st.set_page_config(layout="wide", page_title="BikeSpotter - мониторинг велопотока")

SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vQW_HFsvzJzCctICf5nIdonbSNujkQUuPbc9SepxI2GHeRF-xlWpVHBbSxxXjPKO3QdvxSRsekNBGRR/pub?output=csv"

@st.cache_data(ttl=60)
def load_data():
    df = pd.read_csv(SHEET_CSV_URL)
    df.columns = df.columns.str.strip()
    
    clean_date = df['date'].astype(str).str.split(' ').str[0]
    clean_time = df['time'].astype(str).str.strip()
    df['dateTime'] = pd.to_datetime(clean_date + ' ' + clean_time, errors='coerce')
    df = df.dropna(subset=['dateTime'])
    df['hour'] = df['dateTime'].dt.hour
    
    for col in ['latitude', 'longitude']:
        df[col] = df[col].astype(str).str.replace(',', '.')
        df[col] = pd.to_numeric(df[col], errors='coerce')
    
    df = df.dropna(subset=['latitude', 'longitude'])
    df = df[(df['latitude'].between(-90, 90)) & (df['longitude'].between(-180, 180))]
    df = df[(df['latitude'] != 0) & (df['longitude'] != 0)]
    
    return df
# Создание страниц
tab_map, tab_stats, tab_about = st.tabs(["Главная", "Цифры", "О проекте"])

# Главная

with tab_map:
    df = load_data()

    st.sidebar.header("Сайт в разработке")
    st.sidebar.caption("Наполнение сайта может меняться. Также возможно изменение методики расчёта интенсивности")

# Фильтры
    st.sidebar.header("Фильтры")

    if not df.empty:
        map_mode = st.sidebar.radio("Режим карты", ["Интенсивность (проездов/ч)", "Количество событий"])
        event = st.sidebar.radio("Тип события", ["Проезд", "Парковка"])
        city_query = st.sidebar.text_input(
        "Поиск города / адреса",
        placeholder="Город, район, улица")
        # st.sidebar.caption("Место для подсказки")

        min_date = df['dateTime'].dt.date.min()
        max_date = df['dateTime'].dt.date.max()

        if min_date == max_date:
            selected_date = st.sidebar.date_input("Дата", min_date)
            date_start = date_end = selected_date
        else:
            date_range = st.sidebar.date_input("Диапазон дат", [min_date, max_date])
            if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
                date_start, date_end = date_range
            else:
                date_start, date_end = min_date, max_date

        hours = st.sidebar.slider("Часы суток", 0, 23, (0, 23))

    #Установка возможных размеров гексагонов и размер по-умолчанию
        resolution = st.slider("Размер гексагона", min_value=5, max_value=12, value=10) 
        st.sidebar.caption("Рекомендации: 5-6 для межгорода, 7-9 между крупными точками, 10 для перемещений по району, 12 для детального анализа (только для фильтра Количество событий)")
        if map_mode == "Интенсивность": # Ограничиваем размер гексагона для корректного расчёта и отображения при выбранном фильтре Интенсивность
            effective_res = max(resolution, 10)
        else:
            effective_res = resolution

    
        filtered_df = df[
            (df['eventType'] == event) & 
            (df['hour'] >= hours[0]) & (df['hour'] <= hours[1]) &
            (df['dateTime'].dt.date >= date_start) & (df['dateTime'].dt.date <= date_end)
        ]

    # Расчёт интенсивности
        if not filtered_df.empty:
            filtered_df['h3'] = filtered_df.apply(
                lambda r: h3.latlng_to_cell(r['latitude'], r['longitude'], effective_res), axis=1
            )
        
            filtered_df['date_hour'] = filtered_df['dateTime'].dt.floor('h')

            H3_SIZES_KM = {
                5: 14.79128, 6: 5.59436, 7: 2.11304, 8: 0.79672,
                9: 0.29444, 10: 0.114312
            } # Список длин граней гексагонов, умноженных на 1,732
        
            def calc_session_rate(group, effective_res, walk_speed_kmh=4, discount=0.75):
                n = len(group)
                duration_min = (group['dateTime'].max() - group['dateTime'].min()).total_seconds() / 60.0
                duration_min = max(duration_min, 1.0)
            
                is_pro = group['is_pro'].any() if 'is_pro' in group.columns else False
            
                if duration_min >= 10.0 or is_pro:
                    return (n * 60.0) / duration_min # Для профессиональных или долгих замеров нам не нужно знать, сколько времени человек провёл в гексагоне
                else:
                    hex_size_km = H3_SIZES_KM.get(effective_res, 0)
                    walk_time_hours = hex_size_km / walk_speed_kmh
                # Применяем формула + понижающий коэффициент для непрофессиональных коротких замеров
                    return ((1.0 / walk_time_hours) * n) * discount

        # Выбор логики: если выбраны события ИЛИ в типе события есть "парк"
            if map_mode == "Количество событий" or "парк" in str(event).lower():
                hex_df = filtered_df.groupby('h3', as_index=False).size().rename(columns={'size': 'count'})
                tooltip_txt = "Количество событий: {count}"
            else:
                session_rates = filtered_df.groupby(['date_hour', 'h3']).apply(calc_session_rate, effective_res=effective_res).reset_index()
                session_rates.columns = ['date_hour', 'h3', 'rate']
                hex_df = session_rates.groupby('h3', as_index=False)['rate'].mean()
                hex_df.columns = ['h3', 'count']
                hex_df['count'] = hex_df['count'].round(1)
                tooltip_txt = "Интенсивность: {count} в час"

        # Определение координат и ViewState
            map_lat = filtered_df['latitude'].mean()
            map_lon = filtered_df['longitude'].mean()

            if city_query:
                try:
                    geolocator = Nominatim(user_agent="sim_tracker_app")
                    location = geolocator.geocode(city_query)
                    if location:
                        map_lat, map_lon = location.latitude, location.longitude
                except Exception:
                    pass

            view_state = pdk.ViewState(latitude=map_lat, longitude=map_lon, zoom=13, pitch=0)

            # Создание слоя
            layer = pdk.Layer(
                 "H3HexagonLayer",
                hex_df,
                get_hexagon="h3",
                get_fill_color="[255, (1 - count / 20) * 255, 0, 180]",
                pickable=True,
                extruded=False,
            )

        # Отрисовка карты
            if resolution > 10:
                st.sidebar.caption("Выберите другой размер или смените фильтр Интенсивность на Количество событий")
            else:
                st.pydeck_chart(pdk.Deck(
                    layers=[layer],
                    initial_view_state=view_state,
                    tooltip={"text": tooltip_txt}
                    ))
        
            map_lat = filtered_df['latitude'].mean()
            map_lon = filtered_df['longitude'].mean()

            if city_query:
                try:
                    geolocator = Nominatim(user_agent="sim_tracker_app")
                    location = geolocator.geocode(city_query)
                    if location:
                        map_lat, map_lon = location.latitude, location.longitude
                    else:
                        st.sidebar.warning("Локация не найдена")
                except Exception:
                    st.sidebar.error("Ошибка сервиса геокодинга")

            view_state = pdk.ViewState(
                latitude=map_lat,
                longitude=map_lon,
                zoom=10,
                pitch=0
            )
        
        else:
            st.warning("Нет данных по выбранным фильтрам")

        st.subheader("Сводка по дням")
    if not filtered_df.empty:
        daily = filtered_df.groupby(filtered_df['dateTime'].dt.date).agg(
            Всего_событий=('eventType', 'count'),
            Задействовано_зон=('h3', 'nunique')
        )
        st.dataframe(daily, use_container_width=True)
    else:
        st.error("В таблице нет корректных данных.")


# Страница "Цифры"
with tab_stats:
    st.header("Статистика")
    
    # Метрики в один ряд
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Всего записей", len(filtered_df))
    
    park_count = filtered_df['eventType'].astype(str).str.contains('парк', case=False).sum()
    c2.metric("Парковки", park_count)
    c3.metric("Проезды", len(filtered_df) - park_count)
    
    users = filtered_df['userId'].nunique() if 'userId' in filtered_df.columns else 0
    c4.metric("Счётчиков", users)
    
    # Линейный график по дням
    if 'receivedAt' in filtered_df.columns:
        st.subheader("Динамика поступивших данных")
        chart_df = filtered_df.copy()
        chart_df['day'] = pd.to_datetime(chart_df['receivedAt']).dt.date
        st.line_chart(chart_df.groupby('day').size())

# Страница "О проекте"

with tab_about:
    st.header("О проекте")
    st.write("В разработке")
