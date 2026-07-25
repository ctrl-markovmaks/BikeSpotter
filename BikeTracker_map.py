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
    st.sidebar.caption("Сайт энтузиаста для энтузиастов")

# Фильтры
    st.sidebar.header("Фильтры")

    if not df.empty:
        map_mode = st.sidebar.radio("Режим карты", ["Интенсивность (проездов/ч)", "Количество событий"])
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

    # Установка возможных размеров гексагонов и размер по умолчанию
        resolution = st.sidebar.slider("Размер гексагона", min_value=5, max_value=12, value=10) 
        st.sidebar.caption("Рекомендации: 5-6 для межгорода, 7-9 между крупными точками, 10 для перемещений по району, 12 для детального анализа (только для фильтра Количество событий)")

# Проверяем, выбран ли режим интенсивности для проезда
        is_parking = "парк" in str(event).lower()
        is_intensity_drive = (map_mode == "Интенсивность") and (not is_parking)

# Если выбрана Интенсивность+Проезд и размер 11 или 12 — скрываем карту и выводим предупреждение
        if is_intensity_drive and resolution > 10:
            st.warning("Расчёт интенсивности для проездов доступен только для размеров гексагона от 5 до 10. Пожалуйста, уменьшите размер или переключите режим на 'Количество событий'.")
        else:
            effective_res = resolution

        filtered_df = df[
            (df['eventType'] == event) & 
            (df['hour'] >= hours[0]) & (df['hour'] <= hours[1]) &
            (df['dateTime'].dt.date >= date_start) & (df['dateTime'].dt.date <= date_end)
            ]

        if not filtered_df.empty:
            filtered_df['h3'] = filtered_df.apply(
                lambda r: h3.latlng_to_cell(r['latitude'], r['longitude'], effective_res), axis=1
            )
        
            filtered_df['date_hour'] = filtered_df['dateTime'].dt.floor('h')

            H3_SIZES_KM = {
                5: 14.79128, 6: 5.59436, 7: 2.11304, 8: 0.79672,
                9: 0.29444, 10: 0.114312
            } # Длины граней гексагонов * 1,732
            
            def calc_session_rate(group, effective_res, walk_speed_kmh=4, discount=0.75):
                duration_min = (group['dateTime'].max() - group['dateTime'].min()).total_seconds() / 60.0
                is_pro = group['is_pro'].any() if 'is_pro' in group.columns else False
                    
                # 1. Определение уровня достоверности
                if is_pro or duration_min >= 15.0:
                    conf = "Высокая"
                elif duration_min >= 5.0:
                    conf = "Средняя"
                else:
                    conf = "Низкая"
        
    # 2. Если уровень не выбран в фильтре — пропускаем замер
                if conf not in selected_conf:
                    return None
        
    # 3. Расчёт интенсивности
                n = len(group)
                duration_calc = max(duration_min, 1.0)
    
                if duration_calc >= 10.0 or is_pro:
                    return (n * 60.0) / duration_calc
                else:
                    hex_size_km = H3_SIZES_KM.get(effective_res, 0)
                    walk_time_hours = hex_size_km / walk_speed_kmh
                    return ((1.0 / walk_time_hours) * n) * discount

        # Выбор логики отображения
                if map_mode == "Количество событий" or is_parking:
                    hex_df = filtered_df.groupby('h3', as_index=False).size().rename(columns={'size': 'count'})
                    tooltip_txt = "Количество событий: {count}"
                else:
                    session_rates = filtered_df.groupby(['date_hour', 'h3']).apply(calc_session_rate, effective_res=effective_res).reset_index()
                    session_rates = session_rates.dropna(subset=['rate'])
                    session_rates.columns = ['date_hour', 'h3', 'rate']
                    hex_df = session_rates.groupby('h3', as_index=False)['rate'].mean()
                    hex_df.columns = ['h3', 'count']
                    hex_df['count'] = hex_df['count'].round(1)
                    tooltip_txt = "Интенсивность: {count} в час"

        # Определение центра карты
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

                view_state = pdk.ViewState(latitude=map_lat, longitude=map_lon, zoom=13, pitch=0)

                layer = pdk.Layer(
                    "H3HexagonLayer",
                    hex_df,
                    get_hexagon="h3",
                    get_fill_color="[255, (1 - count / 20) * 255, 0, 180]",
                    pickable=True,
                    extruded=False,
                )

                st.pydeck_chart(pdk.Deck(
                    layers=[layer],
                    initial_view_state=view_state,
                    tooltip={"text": tooltip_txt}
                ))
            else:
                st.warning("Нет данных по выбранным фильтрам")


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
    st.write("В разработке")
