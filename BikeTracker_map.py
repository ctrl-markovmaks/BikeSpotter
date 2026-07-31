import streamlit as st
import pandas as pd
import pydeck as pdk
import numpy as np
import gspread
import osmnx as ox
import geopandas as gpd
from datetime import datetime
from geopy.geocoders import Nominatim

st.set_page_config(layout="wide", page_title="BikeSpotter - мониторинг велопотока")

gc = gspread.service_account_from_dict(dict(st.secrets["gcp_service_account"]))
sh = gc.open_by_url("https://docs.google.com/spreadsheets/d/1fa5x5gLaK-7aOAd0DgtIGX_A8EM3qFN3rwVPHEd6rHM/edit")
worksheet = sh.sheet1

@st.cache_data(ttl=60)
def load_data():
    data = worksheet.get_all_values()
    df = pd.DataFrame(data[1:], columns=data[0])
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
# Посмотреть, что видит сайт
# raw_data = worksheet.get_all_values()
# st.write(f"Найдено строк: {len(raw_data)}")
# st.dataframe(raw_data)

# Кэширование загрузки графа OSMnx, чтобы не скачивать карту заново при каждом переключении фильтров ###############################

@st.cache_resource(ttl=3600)
def load_osm_graph(lat, lon, dist=2000):
    lat_r, lon_r = round(float(lat), 2), round(float(lon), 2)
    # network_type='all' скачивает автодороги, тротуары, велодорожки и дворовые проезды
    G = ox.graph_from_point((lat_r, lon_r), dist=dist, network_type='all')
    return G

# Плашка с информацией. background-color - цвет подложки, color - цвет текста, padding, border-radius, font-weight. Цвета в HEX формате
st.markdown(
    """
    <div style="background-color: #d9d321; color: #000000; padding: 12px; border-radius: 8px; font-weight: 500;">
        Сайт в разработке. Его наполнение и метод расчёта интенсивности может меняться. Используйте данные об интенсивности для ознакомления.
    </div>
    """,
    unsafe_allow_html=True
) # Просьба присылать фото для добавления данных
st.link_button("📸 Прислать фото (Google Формы)", "https://docs.google.com/forms/d/e/1FAIpQLSeEpM74U7KLOYIIAYUdZnjj-voIFrFby6Nf-GTpCIxmRiJPvw/viewform?usp=publish-editor")
# Создание страниц
tab_map, tab_stats, tab_about = st.tabs(["Главная", "Цифры", "О проекте"])

# Главная

with tab_map:
    df = load_data()

    st.sidebar.header("Bike Spotter")
    st.sidebar.caption("Собираем данные о велодвижении в городах")

# Фильтры
    st.sidebar.header("Фильтры")

    if not df.empty:
        map_mode = st.sidebar.radio("Режим карты", ["Интенсивность", "Количество событий"])
        event = st.sidebar.radio("Тип события", ["Проезд", "Парковка"])
        city_query = st.sidebar.text_input(
        "Поиск города / адреса",
        placeholder="Город, район, улица")
        # st.sidebar.caption("Место для подсказки")
        st.sidebar.subheader("Достоверность данных")
        conf_low = st.sidebar.checkbox("Низкая (< 5 мин)")
        conf_med = st.sidebar.checkbox("Средняя (5–15 мин)")
        conf_high = st.sidebar.checkbox("Высокая (≥ 15 мин / Pro)")

        # Собираем выбранные варианты
        selected_conf = []
        if conf_low: selected_conf.append("Низкая")
        if conf_med: selected_conf.append("Средняя")
        if conf_high: selected_conf.append("Высокая")
        # Если ничего не выбрано — показываем все
        if not selected_conf:
            selected_conf = ["Низкая", "Средняя", "Высокая"]

        st.sidebar.subheader("Свежесть данных")
        f_fresh = st.sidebar.checkbox("Свежие (до 10 дней)", value=True)
        f_mid = st.sidebar.checkbox("Средние (10–30 дней)", value=True)
        f_old = st.sidebar.checkbox("Старые (> 30 дней)", value=True)

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

# Проверяем, выбран ли режим интенсивности для проезда
        is_parking = "парк" in str(event).lower()
        is_intensity_drive = (map_mode == "Интенсивность") and (not is_parking)

        filtered_df = df[
            (df['eventType'] == event) & 
            (df['hour'] >= hours[0]) & (df['hour'] <= hours[1]) &
            (df['dateTime'].dt.date >= date_start) & (df['dateTime'].dt.date <= date_end)
            ]
        # определение свежести данных
        now = pd.Timestamp.now()
        days = (now - filtered_df['dateTime']).dt.days
        mask = (f_fresh & (days <= 10)) | (f_mid & days.between(11, 30)) | (f_old & (days > 30))
        filtered_df = filtered_df[mask]

        if not filtered_df.empty:
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

            with st.spinner("Загрузка сети улиц и тротуаров из OpenStreetMap..."):
                try:
                    G = load_osm_graph(map_lat, map_lon, dist=search_radius)
                    edges_gdf = ox.graph_to_gdfs(G, nodes=False)

                    # Автоматически привязываем точки к ближайшему отрезку OSM
                    nearest_e = ox.nearest_edges(G, X=filtered_df['longitude'].values, Y=filtered_df['latitude'].values)
                    filtered_df['edge_id'] = [f"{u}_{v}_{k}" for u, v, k in nearest_e]

                    filtered_df['date_hour'] = filtered_df['dateTime'].dt.floor('h')

                    def format_last_seen(dt):
                        d = (pd.Timestamp.now() - dt).days
                        return f"Было {d} дн. назад в {dt.strftime('%H:%M (%d.%m.%Y)')}"

                    def calc_session_rate(group, discount=0.75):
                        duration_min = (group['dateTime'].max() - group['dateTime'].min()).total_seconds() / 60.0
                        is_pro = group['is_pro'].any() if 'is_pro' in group.columns else False
                        
                        if is_pro or duration_min >= 15.0:
                            conf = "Высокая"
                        elif duration_min >= 5.0:
                            conf = "Средняя"
                        else:
                            conf = "Низкая"

                        n = len(group)
                        duration_calc = max(duration_min, 1.0)

                        if duration_calc >= 10.0 or is_pro:
                            rate = (n * 60.0) / duration_calc
                        else:
                            rate = (n * 12.0) * discount

                        return pd.Series({
                            'rate': rate, 
                            'conf': conf, 
                            'last_dt': group['dateTime'].max()
                        })

                    if map_mode == "Количество событий" or is_parking:
                        edge_stats = filtered_df.groupby('edge_id', as_index=False).agg(
                            count=('dateTime', 'size'),
                            last_dt=('dateTime', 'max')
                        )
                        edge_stats['last_seen'] = edge_stats['last_dt'].apply(format_last_seen)
                        tooltip_txt = "<b>Количество событий:</b> {count}<br/>{last_seen}"
                        has_data = not edge_stats.empty
                    else:
                        session_rates = filtered_df.groupby(['date_hour', 'edge_id']).apply(calc_session_rate).reset_index()
                        session_rates = session_rates[session_rates['conf'].isin(selected_conf)]

                        if session_rates.empty:
                            st.warning("Нет данных с выбранным уровнем достоверности")
                            has_data = False
                        else:
                            edge_stats = session_rates.groupby('edge_id', as_index=False).agg(
                                count=('rate', 'mean'),
                                conf=('conf', lambda x: ', '.join(x.unique())),
                                last_dt=('last_dt', 'max')
                            )
                            edge_stats['count'] = edge_stats['count'].round(1)
                            edge_stats['last_seen'] = edge_stats['last_dt'].apply(format_last_seen)
                            tooltip_txt = "<b>Интенсивность:</b> {count} в час<br/><b>Достоверность:</b> {conf}<br/>{last_seen}"
                            has_data = True

                    if has_data:
                        # Сопоставляем агрегированную статистику с геометрией улиц
                        edges_gdf['edge_id'] = [f"{u}_{v}_{k}" for u, v, k in edges_gdf.index]
                        map_data = edges_gdf.merge(edge_stats, on='edge_id', how='inner')

                        # Преобразуем координаты отрезка в формат для PyDeck
                        map_data['path'] = map_data['geometry'].apply(lambda geom: [list(coord) for coord in geom.coords])

                        # Задаем цвет отрезка (от желтого к красному в зависимости от нагрузки)
                        max_cnt = max(map_data['count'].max(), 1)
                        map_data['color'] = map_data['count'].apply(
                            lambda c: [255, int((1 - min(c / max_cnt, 1.0)) * 255), 0, 220]
                        )

                        view_state = pdk.ViewState(latitude=map_lat, longitude=map_lon, zoom=14, pitch=0)

                        layer = pdk.Layer(
                            "PathLayer",
                            map_data,
                            get_path="path",
                            get_color="color",
                            get_width=6,
                            width_min_pixels=3,
                            pickable=True,
                        )

                        st.pydeck_chart(pdk.Deck(
                            map_style="light",
                            layers=[layer],
                            initial_view_state=view_state,
                            tooltip={"html": tooltip_txt}
                        ))

                except Exception as e:
                    st.error(f"Ошибка при обработке графа улиц OSM: {e}")
        else:
            st.warning("Нет данных по выбранным фильтрам")
    else:
        st.warning("Таблица пока пуста")

# Страница "Цифры"
with tab_stats:
    st.header("Статистика")
    
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Всего записей", len(df))
    
    park_count = df['eventType'].astype(str).str.contains('парк', case=False).sum()
    c2.metric("Парковки", park_count)
    c3.metric("Проезды", len(df) - park_count)
    
    tech_id = "00000000-0000-0000-0000-000000000000"
    if 'userId' in df.columns:
        valid_users = df['userId'].dropna().astype(str).str.strip()
        users = valid_users[valid_users != tech_id].nunique()
    else:
        users = 0

    c4.metric("Счётчиков", users)
    
    if 'receivedAt' in df.columns:
        st.subheader("Динамика поступивших данных")
        chart_df = df.copy()
        chart_df['day'] = pd.to_datetime(chart_df['receivedAt']).dt.date
        st.line_chart(chart_df.groupby('day').size())

# Страница "О проекте"

with tab_about:
    st.header("О проекте")
    st.write("Данный проект создан энтузиастом, которому однажды стало интересно, сколько велосипедистов и электросамокатчиков проезжает мимо него за прогулку. По началу использовался обычный счётчик в телефоне, но в июле 2026 он попытался создать мобильное приложение и сайт, чтобы визуализировать точки, где проехало больше всего любителей новой мобильности. Так появился BikeSpotter")
    st.subheader("Сбор данных")
    st.write("Сбор данных осуществляется двумя способами:")
    st.write("- С помощью приложения для учёта проехавших или припаркованных СИМ и велосипедов")
    st.write("- с помощью форму обратной связи")
    st.write("Приложение представляет из себя обычный кликер, где по нажатию фиксируются GPS координаты и тип события (проезд или парковка), а после нажатия кнопки Отправить передаёт накопившиеся с последней отправки данные в таблицу, откуда сайт берёт данные для отрисовки и расчётов. Сбор данных через Google форму осуществляется в ту же таблицу, но уже вручную.")
    st.subheader("Метод расчёта интенсивности")
    st.write("В разработке")
    st.subheader("Достоверность данных")
    st.write("Так как интенсивность считается в течении длительного времени (обычно 15 или более минут), а идея BikeSpotter в мгновенных измерениях во время прогулки, то возникла потребность в том, чтобы разделять откровенно сырые данные от более-менее правдивых. Для этого введена система достоверности данных. Чем дольше вёлся учёт в конкретной ячейке, тем более качественные данные об интенсивности получаются. На качество данных о припаркованных средств мобильности длительность наблюдения не влияет, так как мы не приводим их к какой-то величине и оставляем как есть (а если бы приводили, то эти данные были бы бесполезными, так как нет такого показателя как «припаркованных в час»)")

