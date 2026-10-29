/*
 * Струм (Strum) — Lovelace cards for the electros_ev integration (smart ElectroS charging).
 *
 * Classic script (no imports, no CDN). Served by the integration at /electros_ev/electros-card.js.
 * Defines:
 *   <electros-card>  full card (power ring, countdown, quick charge by time or energy, amps, night tariff,
 *                    energy statistics День · Тиждень · Місяць · Рік from HA long-term statistics)
 *   <electros-tile>  compact widget (size: small | medium)
 *   <electros-card-editor>, <electros-tile-editor>  visual editors built on <ha-form>
 *
 * Entities are resolved strictly by the shared contract:
 *   hass.entities[id].device_id === config.device_id && hass.entities[id].translation_key === KEY
 * (the entity domain is matched as well, so the lookup key is "<domain>.<translation_key>").
 * Entity-provided strings are only ever written through textContent / attributes, never innerHTML.
 */
(() => {
  'use strict';

  const VERSION = '2.1.0';
  const DOMAIN = 'electros_ev';
  const DOCS_URL = 'https://github.com/VladySpidy1/Strum-electros';

  const STATUSES = [
    'waiting_for_vehicle', 'vehicle_connected_idle', 'charging', 'charging_fault', 'charging_blocked',
    'mains_voltage_low', 'charger_communication_fault', 'offline', 'leakage_current_fault',
    'overcurrent_fault', 'unknown',
  ];
  const FAULTS = new Set([
    'charging_fault', 'mains_voltage_low', 'charger_communication_fault',
    'leakage_current_fault', 'overcurrent_fault',
  ]);
  const DEFAULT_QUICK = [15, 30, 60, 120];
  const QUICK_OPTIONS = [5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 300, 360, 480];
  const DEFAULT_QUICK_KWH = [5, 10, 20, 30];
  const QUICK_KWH_OPTIONS = [2, 5, 10, 15, 20, 25, 30, 40, 50, 60, 80];
  const KWH_MIN = 0.5; // electros_ev.charge_energy accepts 0.5..100 kWh
  const KWH_MAX = 100;
  const EXTEND_MIN = 15; // "+15 хв"
  const EXTEND_KWH = 5; // "+5 кВт·год"
  const PERIODS = ['day', 'week', 'month', 'year'];
  const STAT_TTL = 60000; // the current period is re-read at most once a minute
  const STAT_FINAL_AFTER = 70 * 60000; // a past period read this long after its end is final
  const RING_R = 116;
  const RING_C = 2 * Math.PI * RING_R; // 728.85
  const TILE_R = 38;
  const TILE_C = 2 * Math.PI * TILE_R; // 238.76
  const DEFAULT_START = 23 * 60;
  const DEFAULT_END = 7 * 60;

  /* ------------------------------------------------------------------ i18n */

  const I18N = {
    uk: {
      st_waiting_for_vehicle: 'Чекаю авто',
      st_vehicle_connected_idle: 'Авто підключено',
      st_charging: 'Заряджається',
      st_charging_fault: 'Помилка зарядки',
      st_charging_blocked: 'Заблоковано таймером',
      st_mains_voltage_low: 'Низька напруга мережі',
      st_charger_communication_fault: "Помилка зв'язку",
      st_offline: 'Офлайн',
      st_leakage_current_fault: 'Витік струму',
      st_overcurrent_fault: 'Перевищення струму',
      st_unknown: 'Невідомо',
      fh_charging_fault: 'Перевірте кабель і авто',
      fh_mains_voltage_low: 'Напруга нижча за мінімум',
      fh_charger_communication_fault: 'Зарядка не чує авто',
      fh_leakage_current_fault: 'Спрацював захист від витоку',
      fh_overcurrent_fault: 'Струм перевищив 40 А',
      waiting_night: 'Чекає нічного тарифу',
      stopped: 'Зупинено',
      no_link: "Немає зв'язку",
      offline_since: 'офлайн з {t}',
      plug_in: 'Підключіть кабель',
      cable_not_connected: 'кабель не підключено',
      ready: 'готово до заряду',
      blocked_big: 'Заборонено',
      blocked_short: 'Заблоковано',
      blocked_sub: 'можна зарядити зараз',
      start_in: 'старт через {d}',
      in_d: 'через {d}',
      night_from: 'нічний тариф з {t}',
      night_until: 'нічний тариф до {t}',
      any_time: 'можна заряджати будь-коли',
      kw: 'кВт',
      kwh: 'кВт·год',
      a: 'А',
      v: 'В',
      dur_hm: '{h} год {m} хв',
      dur_h: '{h} год',
      dur_m: '{m} хв',
      chip_m: '{m} хв',
      chip_h: '{h} год',
      less_min: 'менше хвилини',
      no_limit: 'Без ліміту',
      no_limit_l: 'без ліміту',
      until_stop_at: 'до зупинки о {t}',
      until_full: 'до повного заряду',
      until_t: 'до {t}',
      schedule_left: 'нічний тариф · ще {d}',
      left_d: 'ще {d}',
      plus15: '+15 хв',
      plus15_aria: 'Додати 15 хвилин',
      session: 'Сесія',
      duration: 'Триває',
      cost: 'Вартість',
      voltage: 'Напруга',
      charge_for: 'Зарядити на',
      then_off: 'потім вимкнеться сама',
      until_manual: 'до зупинки вручну',
      stop: 'Зупинити',
      start: 'Почати зарядку',
      charge_n: 'Зарядити {d}',
      starting: 'Запускаю…',
      retry: 'Спробувати ще раз',
      stopping: 'Зупиняю…',
      slide: 'Проведіть, щоб зарядити',
      slide_aria: 'Зарядити {d}: проведіть вправо або натисніть Enter',
      slide_aria_inf: 'Почати зарядку без ліміту: проведіть вправо або натисніть Enter',
      need_now: 'Потрібно зараз?',
      need_now_text: 'Зарядка обійде нічний таймер на обраний час, а потім сама його поверне.',
      current: 'Струм',
      current_aria: 'Струм зарядки',
      night: 'Нічний тариф',
      night_off: 'вимкнено — можна заряджати будь-коли',
      daily: 'щодня',
      weekdays: 'по буднях',
      active_left: 'діє ще {d}',
      weekend_today: 'вихідний — без обмежень',
      edit_hours: 'Змінити години нічного тарифу',
      start_lbl: 'Початок',
      end_lbl: 'Кінець',
      done: 'Готово',
      bypass_at: 'Заряджаємо поза розкладом. Нічний таймер повернеться сам о {t}.',
      bypass_stop: 'Заряджаємо поза розкладом. Нічний таймер повернеться сам після зупинки.',
      weekend: 'Вихідні без обмежень',
      energy: 'Енергія',
      now_bar: 'зараз',
      no_sessions: 'Ще немає сесій зарядки',
      settings: 'Налаштування',
      empty_pick_title: 'Оберіть зарядку',
      empty_pick_text: 'Вкажіть пристрій ElectroS у налаштуваннях картки.',
      empty_nf_title: 'Зарядку не знайдено',
      empty_nf_text: 'Перевірте, що інтеграцію ElectroS налаштовано і пристрій існує.',
      loading: 'Завантаження…',
      other_time: 'Інший час: {d}',
      timer_off: 'таймер вимкнено',
      error_call: 'ElectroS: не вдалося — {e}',
      ring_aria: '{v} {u}, {s}',
      energy_aria: 'Енергія останніх сесій',
      ed_device_id: 'Зарядка',
      ed_theme: 'Тема',
      ed_quick_minutes: 'Швидкий вибір часу, хв',
      ed_quick_kwh: 'Швидкий вибір енергії, кВт·год',
      ed_size: 'Розмір плитки',
      theme_auto: 'Авто (як у Home Assistant)',
      theme_light: 'Світла',
      theme_dark: 'Темна',
      size_small: 'Мала (квадрат)',
      size_medium: 'Середня (широка)',
      seg_time: 'Час',
      seg_energy: 'Енергія',
      mode_aria: 'Як заряджати: за часом чи за енергією',
      charge_kwh: 'Зарядити {k}',
      slide_aria_kwh: 'Зарядити {k}: проведіть вправо або натисніть Enter',
      approx: '≈ {v}',
      of_target: '{a} з {b}',
      eta_at: '≈ о {t}',
      eta_unknown: 'час уточнюється',
      plus_kwh_aria: 'Додати {k}',
      bypass_energy: 'Заряджаємо поза розкладом. Нічний таймер повернеться сам після {k}.',
      le_charging_blocked: 'Зарядка не почалась — її заблокував таймер',
      le_deadline: 'Зупинено за запобіжним лімітом часу — ціль не досягнуто',
      le_stop_failed: 'Не вдалося зупинити зарядку — повторимо, щойно зарядка відповість',
      le_generic: 'Помилка: {e}',
      p_day: 'День',
      p_week: 'Тиждень',
      p_month: 'Місяць',
      p_year: 'Рік',
      period_aria: 'Період статистики',
      prev_aria: 'Попередній період',
      next_aria: 'Наступний період',
      today: 'Сьогодні',
      yesterday: 'Вчора',
      tariff_day: 'Дорога · день',
      tariff_night: 'Дешева · ніч',
      no_split: 'Без розбивки',
      no_split_note: 'лічильник зарядки, без розбивки на тарифи',
      total: 'Усього',
      savings: 'Економія на нічному тарифі',
      stat_empty: 'Статистика накопичується з моменту встановлення',
      stat_empty_sub: 'Перші дані зʼявляться впродовж години.',
      stat_error: 'Не вдалося завантажити статистику',
      stat_aria: 'Енергія за період {p}: {v}. Стрілки вліво і вправо — по стовпчиках.',
    },
    ru: {
      st_waiting_for_vehicle: 'Жду авто',
      st_vehicle_connected_idle: 'Авто подключено',
      st_charging: 'Заряжается',
      st_charging_fault: 'Ошибка зарядки',
      st_charging_blocked: 'Заблокировано таймером',
      st_mains_voltage_low: 'Низкое напряжение сети',
      st_charger_communication_fault: 'Ошибка связи',
      st_offline: 'Офлайн',
      st_leakage_current_fault: 'Утечка тока',
      st_overcurrent_fault: 'Превышение тока',
      st_unknown: 'Неизвестно',
      fh_charging_fault: 'Проверьте кабель и авто',
      fh_mains_voltage_low: 'Напряжение ниже минимума',
      fh_charger_communication_fault: 'Зарядка не видит авто',
      fh_leakage_current_fault: 'Сработала защита от утечки',
      fh_overcurrent_fault: 'Ток превысил 40 А',
      waiting_night: 'Ждёт ночного тарифа',
      stopped: 'Остановлено',
      no_link: 'Нет связи',
      offline_since: 'офлайн с {t}',
      plug_in: 'Подключите кабель',
      cable_not_connected: 'кабель не подключён',
      ready: 'готово к зарядке',
      blocked_big: 'Запрещено',
      blocked_short: 'Заблокировано',
      blocked_sub: 'можно зарядить сейчас',
      start_in: 'старт через {d}',
      in_d: 'через {d}',
      night_from: 'ночной тариф с {t}',
      night_until: 'ночной тариф до {t}',
      any_time: 'можно заряжать в любое время',
      kw: 'кВт',
      kwh: 'кВт·ч',
      a: 'А',
      v: 'В',
      dur_hm: '{h} ч {m} мин',
      dur_h: '{h} ч',
      dur_m: '{m} мин',
      chip_m: '{m} мин',
      chip_h: '{h} ч',
      less_min: 'меньше минуты',
      no_limit: 'Без лимита',
      no_limit_l: 'без лимита',
      until_stop_at: 'до остановки в {t}',
      until_full: 'до полной зарядки',
      until_t: 'до {t}',
      schedule_left: 'ночной тариф · ещё {d}',
      left_d: 'ещё {d}',
      plus15: '+15 мин',
      plus15_aria: 'Добавить 15 минут',
      session: 'Сессия',
      duration: 'Идёт',
      cost: 'Стоимость',
      voltage: 'Напряжение',
      charge_for: 'Зарядить на',
      then_off: 'потом выключится сама',
      until_manual: 'до ручной остановки',
      stop: 'Остановить',
      start: 'Начать зарядку',
      charge_n: 'Зарядить {d}',
      starting: 'Запускаю…',
      retry: 'Попробовать ещё раз',
      stopping: 'Останавливаю…',
      slide: 'Проведите, чтобы зарядить',
      slide_aria: 'Зарядить {d}: проведите вправо или нажмите Enter',
      slide_aria_inf: 'Начать зарядку без лимита: проведите вправо или нажмите Enter',
      need_now: 'Нужно сейчас?',
      need_now_text: 'Зарядка обойдёт ночной таймер на выбранное время, а потом сама его вернёт.',
      current: 'Ток',
      current_aria: 'Ток зарядки',
      night: 'Ночной тариф',
      night_off: 'выключено — можно заряжать в любое время',
      daily: 'ежедневно',
      weekdays: 'по будням',
      active_left: 'действует ещё {d}',
      weekend_today: 'выходной — без ограничений',
      edit_hours: 'Изменить часы ночного тарифа',
      start_lbl: 'Начало',
      end_lbl: 'Конец',
      done: 'Готово',
      bypass_at: 'Заряжаем вне расписания. Ночной таймер вернётся сам в {t}.',
      bypass_stop: 'Заряжаем вне расписания. Ночной таймер вернётся сам после остановки.',
      weekend: 'Выходные без ограничений',
      energy: 'Энергия',
      now_bar: 'сейчас',
      no_sessions: 'Пока нет сессий зарядки',
      settings: 'Настройки',
      empty_pick_title: 'Выберите зарядку',
      empty_pick_text: 'Укажите устройство ElectroS в настройках карточки.',
      empty_nf_title: 'Зарядка не найдена',
      empty_nf_text: 'Проверьте, что интеграция ElectroS настроена и устройство существует.',
      loading: 'Загрузка…',
      other_time: 'Другое время: {d}',
      timer_off: 'таймер выключен',
      error_call: 'ElectroS: не удалось — {e}',
      ring_aria: '{v} {u}, {s}',
      energy_aria: 'Энергия последних сессий',
      ed_device_id: 'Зарядка',
      ed_theme: 'Тема',
      ed_quick_minutes: 'Быстрый выбор времени, мин',
      ed_quick_kwh: 'Быстрый выбор энергии, кВт·ч',
      ed_size: 'Размер плитки',
      theme_auto: 'Авто (как в Home Assistant)',
      theme_light: 'Светлая',
      theme_dark: 'Тёмная',
      size_small: 'Маленькая (квадрат)',
      size_medium: 'Средняя (широкая)',
      seg_time: 'Время',
      seg_energy: 'Энергия',
      mode_aria: 'Как заряжать: по времени или по энергии',
      charge_kwh: 'Зарядить {k}',
      slide_aria_kwh: 'Зарядить {k}: проведите вправо или нажмите Enter',
      approx: '≈ {v}',
      of_target: '{a} из {b}',
      eta_at: '≈ в {t}',
      eta_unknown: 'время уточняется',
      plus_kwh_aria: 'Добавить {k}',
      bypass_energy: 'Заряжаем вне расписания. Ночной таймер вернётся сам после {k}.',
      le_charging_blocked: 'Зарядка не началась — её заблокировал таймер',
      le_deadline: 'Остановлено по защитному лимиту времени — цель не достигнута',
      le_stop_failed: 'Не удалось остановить зарядку — повторим, как только зарядка ответит',
      le_generic: 'Ошибка: {e}',
      p_day: 'День',
      p_week: 'Неделя',
      p_month: 'Месяц',
      p_year: 'Год',
      period_aria: 'Период статистики',
      prev_aria: 'Предыдущий период',
      next_aria: 'Следующий период',
      today: 'Сегодня',
      yesterday: 'Вчера',
      tariff_day: 'Дорогой · день',
      tariff_night: 'Дешёвый · ночь',
      no_split: 'Без разбивки',
      no_split_note: 'счётчик зарядки, без разбивки по тарифам',
      total: 'Всего',
      savings: 'Экономия на ночном тарифе',
      stat_empty: 'Статистика накапливается с момента установки',
      stat_empty_sub: 'Первые данные появятся в течение часа.',
      stat_error: 'Не удалось загрузить статистику',
      stat_aria: 'Энергия за период {p}: {v}. Стрелки влево и вправо — по столбцам.',
    },
    en: {
      st_waiting_for_vehicle: 'Waiting for car',
      st_vehicle_connected_idle: 'Car connected',
      st_charging: 'Charging',
      st_charging_fault: 'Charging fault',
      st_charging_blocked: 'Blocked by timer',
      st_mains_voltage_low: 'Low mains voltage',
      st_charger_communication_fault: 'Communication fault',
      st_offline: 'Offline',
      st_leakage_current_fault: 'Leakage current',
      st_overcurrent_fault: 'Overcurrent',
      st_unknown: 'Unknown',
      fh_charging_fault: 'Check the cable and the car',
      fh_mains_voltage_low: 'Voltage is below the minimum',
      fh_charger_communication_fault: 'Charger cannot talk to the car',
      fh_leakage_current_fault: 'Leakage protection tripped',
      fh_overcurrent_fault: 'Current went above 40 A',
      waiting_night: 'Waiting for night rate',
      stopped: 'Stopped',
      no_link: 'No connection',
      offline_since: 'offline since {t}',
      plug_in: 'Plug in the cable',
      cable_not_connected: 'cable not connected',
      ready: 'ready to charge',
      blocked_big: 'Blocked',
      blocked_short: 'Blocked',
      blocked_sub: 'you can charge now',
      start_in: 'starts in {d}',
      in_d: 'in {d}',
      night_from: 'night rate from {t}',
      night_until: 'night rate until {t}',
      any_time: 'charge any time',
      kw: 'kW',
      kwh: 'kWh',
      a: 'A',
      v: 'V',
      dur_hm: '{h} h {m} min',
      dur_h: '{h} h',
      dur_m: '{m} min',
      chip_m: '{m} min',
      chip_h: '{h} h',
      less_min: 'under a minute',
      no_limit: 'No limit',
      no_limit_l: 'no limit',
      until_stop_at: 'stops at {t}',
      until_full: 'until full',
      until_t: 'until {t}',
      schedule_left: 'night rate · {d} left',
      left_d: '{d} left',
      plus15: '+15 min',
      plus15_aria: 'Add 15 minutes',
      session: 'Session',
      duration: 'Duration',
      cost: 'Cost',
      voltage: 'Voltage',
      charge_for: 'Charge for',
      then_off: 'then stops by itself',
      until_manual: 'until you stop it',
      stop: 'Stop',
      start: 'Start charging',
      charge_n: 'Charge {d}',
      starting: 'Starting…',
      retry: 'Try again',
      stopping: 'Stopping…',
      slide: 'Slide to charge',
      slide_aria: 'Charge {d}: slide right or press Enter',
      slide_aria_inf: 'Start charging with no limit: slide right or press Enter',
      need_now: 'Need it now?',
      need_now_text: 'Charging will bypass the night timer for the chosen time and then restore it.',
      current: 'Current',
      current_aria: 'Charging current',
      night: 'Night rate',
      night_off: 'off — charge any time',
      daily: 'daily',
      weekdays: 'weekdays',
      active_left: '{d} left',
      weekend_today: 'weekend — no limits',
      edit_hours: 'Change night rate hours',
      start_lbl: 'Start',
      end_lbl: 'End',
      done: 'Done',
      bypass_at: 'Charging outside the schedule. The night timer comes back by itself at {t}.',
      bypass_stop: 'Charging outside the schedule. The night timer comes back once charging stops.',
      weekend: 'Weekends without limits',
      energy: 'Energy',
      now_bar: 'now',
      no_sessions: 'No charging sessions yet',
      settings: 'Settings',
      empty_pick_title: 'Choose a charger',
      empty_pick_text: 'Pick an ElectroS device in the card settings.',
      empty_nf_title: 'Charger not found',
      empty_nf_text: 'Make sure the ElectroS integration is set up and the device exists.',
      loading: 'Loading…',
      other_time: 'Other duration: {d}',
      timer_off: 'timer off',
      error_call: 'ElectroS: failed — {e}',
      ring_aria: '{v} {u}, {s}',
      energy_aria: 'Energy of recent sessions',
      ed_device_id: 'Charger',
      ed_theme: 'Theme',
      ed_quick_minutes: 'Quick durations, min',
      ed_quick_kwh: 'Quick energy amounts, kWh',
      ed_size: 'Tile size',
      theme_auto: 'Auto (follow Home Assistant)',
      theme_light: 'Light',
      theme_dark: 'Dark',
      size_small: 'Small (square)',
      size_medium: 'Medium (wide)',
      seg_time: 'Time',
      seg_energy: 'Energy',
      mode_aria: 'Charge by time or by energy',
      charge_kwh: 'Charge {k}',
      slide_aria_kwh: 'Charge {k}: slide right or press Enter',
      approx: '≈ {v}',
      of_target: '{a} of {b}',
      eta_at: '≈ at {t}',
      eta_unknown: 'estimating time',
      plus_kwh_aria: 'Add {k}',
      bypass_energy: 'Charging outside the schedule. The night timer comes back by itself after {k}.',
      le_charging_blocked: 'Charging did not start — the timer blocked it',
      le_deadline: 'Stopped by the safety time limit — target not reached',
      le_stop_failed: 'Could not stop charging — retrying as soon as the charger responds',
      le_generic: 'Error: {e}',
      p_day: 'Day',
      p_week: 'Week',
      p_month: 'Month',
      p_year: 'Year',
      period_aria: 'Statistics period',
      prev_aria: 'Previous period',
      next_aria: 'Next period',
      today: 'Today',
      yesterday: 'Yesterday',
      tariff_day: 'Peak · day',
      tariff_night: 'Off-peak · night',
      no_split: 'Not split',
      no_split_note: 'charger counter, not split by rate',
      total: 'Total',
      savings: 'Saved with the night rate',
      stat_empty: 'Statistics are collected from the moment of installation',
      stat_empty_sub: 'First data appears within an hour.',
      stat_error: "Couldn't load statistics",
      stat_aria: 'Energy for {p}: {v}. Left and right arrows move between bars.',
    },
  };

  function langOf(hass) {
    const raw = (hass && ((hass.locale && hass.locale.language) || hass.language)) || '';
    const p = String(raw).toLowerCase().split(/[-_]/)[0];
    return I18N[p] ? p : 'uk';
  }

  function tr(lang, key, vars) {
    let s = (I18N[lang] && I18N[lang][key]);
    if (s === undefined) s = I18N.uk[key];
    if (s === undefined) s = key;
    if (vars) s = s.replace(/\{(\w+)\}/g, (_, k) => (vars[k] === undefined || vars[k] === null ? '' : String(vars[k])));
    return s;
  }

  /* ------------------------------------------------------------ formatting */

  const nfCache = new Map();
  function numLocale(hass) {
    const loc = hass && hass.locale;
    const nf = loc && loc.number_format;
    if (nf === 'comma_decimal') return 'en-US';
    if (nf === 'decimal_comma') return 'de-DE';
    if (nf === 'space_comma') return 'fr-FR';
    if (nf === 'system') return undefined;
    return (loc && loc.language) || (hass && hass.language) || 'uk';
  }

  function fmtNum(v, digits, locale) {
    if (v === null || v === undefined || !Number.isFinite(Number(v))) return '—';
    const key = (locale || '') + '|' + digits;
    let f = nfCache.get(key);
    if (!f) {
      try {
        f = new Intl.NumberFormat(locale, { minimumFractionDigits: digits, maximumFractionDigits: digits });
      } catch (e) {
        f = new Intl.NumberFormat(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
      }
      nfCache.set(key, f);
    }
    return f.format(Number(v));
  }

  function fmtMoney(v, hass, digits) {
    const cur = (hass && hass.config && hass.config.currency) || 'UAH';
    const locale = numLocale(hass);
    const key = 'm|' + (locale || '') + '|' + cur + '|' + digits;
    let f = nfCache.get(key);
    if (!f) {
      const base = { style: 'currency', currency: cur, minimumFractionDigits: digits, maximumFractionDigits: digits };
      try {
        f = new Intl.NumberFormat(locale, Object.assign({ currencyDisplay: 'narrowSymbol' }, base));
      } catch (e) {
        try {
          f = new Intl.NumberFormat(locale, base);
        } catch (e2) {
          f = { format: (x) => fmtNum(x, digits, locale) + ' ' + cur };
        }
      }
      nfCache.set(key, f);
    }
    return f.format(Number(v) || 0);
  }

  const pad = (n) => String(n).padStart(2, '0');
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const finite = (v, d) => {
    const n = typeof v === 'number' ? v : parseFloat(v);
    return Number.isFinite(n) ? n : d;
  };

  /* "10" / "2,5" — whole kWh without decimals, otherwise one decimal. */
  function fmtK(v, locale) {
    const r = Math.round(Number(v) * 10) / 10;
    return fmtNum(r, Number.isInteger(r) ? 0 : 1, locale);
  }

  function fmtKwh(lang, v, locale) {
    return fmtK(v, locale) + ' ' + tr(lang, 'kwh');
  }

  /* Money with sensible precision: kopecks below 100, whole units above. */
  function fmtCost(v, hass) {
    return fmtMoney(v, hass, Math.abs(Number(v)) >= 100 ? 0 : 2);
  }

  function parseHM(s) {
    if (typeof s !== 'string') return null;
    const mm = /^\s*(\d{1,2}):(\d{2})/.exec(s);
    if (!mm) return null;
    const h = Number(mm[1]);
    const mi = Number(mm[2]);
    if (h > 23 || mi > 59) return null;
    return h * 60 + mi;
  }

  function fmtHM(min) {
    if (min === null || min === undefined || !Number.isFinite(min)) return '--:--';
    const m = ((Math.round(min) % 1440) + 1440) % 1440;
    return pad(Math.floor(m / 60)) + ':' + pad(m % 60);
  }

  function fmtDur(lang, totalMin) {
    const t = Math.max(0, Math.round(totalMin));
    if (t < 1) return tr(lang, 'less_min');
    const h = Math.floor(t / 60);
    const m = t % 60;
    if (h && m) return tr(lang, 'dur_hm', { h, m });
    if (h) return tr(lang, 'dur_h', { h });
    return tr(lang, 'dur_m', { m });
  }

  function chipLabel(lang, m) {
    if (!m) return '∞';
    if (m < 60) return tr(lang, 'chip_m', { m });
    if (m % 60 === 0) return tr(lang, 'chip_h', { h: m / 60 });
    return Math.floor(m / 60) + ':' + pad(m % 60);
  }

  function fmtCountdown(sec) {
    const s = Math.max(0, Math.floor(sec));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const r = s % 60;
    return h ? h + ':' + pad(m) + ':' + pad(r) : m + ':' + pad(r);
  }

  function fmtElapsed(sec) {
    const s = Math.max(0, Math.floor(sec));
    return Math.floor(s / 3600) + ':' + pad(Math.floor((s % 3600) / 60));
  }

  /* Time zone: the night window lives on the charger/server clock, so all wall-clock maths use
     hass.config.time_zone (falls back to the browser zone). */
  const dtfCache = new Map();
  function tzOf(hass) {
    const tz = hass && hass.config && hass.config.time_zone;
    return typeof tz === 'string' && tz ? tz : undefined;
  }

  function zoneParts(ms, tz) {
    const key = tz || '';
    let f = dtfCache.get(key);
    if (!f) {
      const opts = {
        hourCycle: 'h23', year: 'numeric', month: 'numeric', day: 'numeric',
        hour: 'numeric', minute: 'numeric', second: 'numeric', weekday: 'short',
      };
      try {
        f = new Intl.DateTimeFormat('en-US', Object.assign({ timeZone: tz }, opts));
      } catch (e) {
        f = new Intl.DateTimeFormat('en-US', opts);
      }
      dtfCache.set(key, f);
    }
    const o = {};
    for (const p of f.formatToParts(new Date(ms))) o[p.type] = p.value;
    let h = Number(o.hour);
    if (h === 24) h = 0;
    return { y: +o.year, mo: +o.month, d: +o.day, h, mi: +o.minute, s: +o.second, wd: o.weekday };
  }

  function clockOf(ms, tz) {
    if (!Number.isFinite(ms)) return '--:--';
    const p = zoneParts(ms, tz);
    return pad(p.h) + ':' + pad(p.mi);
  }

  function monthName(lang, month) {
    try {
      return new Intl.DateTimeFormat(lang, { month: 'long' }).format(new Date(2026, month - 1, 15));
    } catch (e) {
      return String(month);
    }
  }

  function inWindow(now, s, e) {
    if (s === e) return true; // degenerate window = always allowed
    if (s < e) return now >= s && now < e;
    return now >= s || now < e;
  }

  const mod1440 = (v) => ((v % 1440) + 1440) % 1440;

  /* ------------------------------------------------------------ statistics */

  /* Offset of `tz` from UTC at `ms`, in ms. */
  function tzOffset(ms, tz) {
    const p = zoneParts(ms, tz);
    return Date.UTC(p.y, p.mo - 1, p.d, p.h, p.mi, p.s) - Math.floor(ms / 1000) * 1000;
  }

  /* Wall clock (y, mo 1-based, d, h) in `tz` -> epoch ms. Day/month overflow is normalised by Date.UTC. */
  function zonedMs(y, mo, d, h, tz) {
    const guess = Date.UTC(y, mo - 1, d, h || 0, 0, 0);
    const first = guess - tzOffset(guess, tz);
    return guess - tzOffset(first, tz);
  }

  const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

  /* Buckets of one statistics view in the HA time zone. offset 0 = the period containing `now`.
     День = hourly (23/24/25 on DST days), Тиждень = Mon..Sun, Місяць = days, Рік = 12 months. */
  function statRange(period, offset, now, tz) {
    const p = zoneParts(now, tz);
    const b = [];
    let stat;
    if (period === 'day') {
      stat = 'hour';
      const s = zonedMs(p.y, p.mo, p.d + offset, 0, tz);
      const e = zonedMs(p.y, p.mo, p.d + offset + 1, 0, tz);
      for (let t = s; t < e; t += 3600000) b.push({ start: t, end: Math.min(e, t + 3600000) });
    } else if (period === 'year') {
      stat = 'month';
      const y = p.y + offset;
      for (let i = 0; i < 12; i += 1) {
        b.push({ start: zonedMs(y, i + 1, 1, 0, tz), end: zonedMs(y, i + 2, 1, 0, tz), year: y, month: i + 1 });
      }
    } else {
      stat = 'day';
      let mo = p.mo;
      let d;
      let n;
      if (period === 'week') {
        d = p.d - Math.max(0, WEEKDAYS.indexOf(p.wd)) + 7 * offset;
        n = 7;
      } else {
        mo = p.mo + offset;
        d = 1;
        n = new Date(Date.UTC(p.y, mo, 0)).getUTCDate();
      }
      for (let i = 0; i < n; i += 1) b.push({ start: zonedMs(p.y, mo, d + i, 0, tz), end: zonedMs(p.y, mo, d + i + 1, 0, tz) });
    }
    return { period, offset, stat, start: b[0].start, end: b[b.length - 1].end, buckets: b, nowYear: p.y };
  }

  function statTime(v) {
    const n = typeof v === 'number' ? v : Date.parse(v);
    if (!Number.isFinite(n)) return NaN;
    return n < 1e11 ? n * 1000 : n; // very old HA sent seconds
  }

  /* Nearest bucket start: rows are stamped at bucket starts; half a bucket of slack absorbs DST/tz quirks. */
  function bucketIndex(b, t) {
    for (let i = 0; i < b.length; i += 1) {
      const half = (b[i].end - b[i].start) / 2;
      if (t >= b[i].start - half && t < b[i].end - half) return i;
    }
    return -1;
  }

  /* recorder/statistics_during_period result -> per-bucket {day, night, cost}. */
  function mergeStats(rg, ids, res) {
    const out = rg.buckets.map(() => ({ day: 0, night: 0, cost: 0, has: false, hasCost: false }));
    const add = (id, field) => {
      const rows = res && typeof res === 'object' && Array.isArray(res[id]) ? res[id] : [];
      for (const row of rows) {
        if (!row || typeof row !== 'object') continue;
        const t = statTime(row.start);
        const v = Number(row.change);
        if (!Number.isFinite(t) || row.change === null || !Number.isFinite(v)) continue;
        const i = bucketIndex(rg.buckets, t);
        if (i < 0) continue;
        out[i][field] += Math.max(0, v);
        if (field === 'cost') out[i].hasCost = true;
        else out[i].has = true;
      }
    };
    add(ids[0], 'day');
    add(ids[1], 'night');
    if (ids[2]) add(ids[2], 'cost');
    return out;
  }

  /* Round an axis maximum up to 1/2/3/4/5/6/8 x 10^n so the half-way gridline is a clean number too. */
  function niceCeil(v) {
    if (!(v > 0)) return 1;
    const e = Math.pow(10, Math.floor(Math.log10(v)));
    for (const f of [1, 2, 3, 4, 5, 6, 8, 10]) if (f * e >= v - 1e-9) return f * e;
    return 10 * e;
  }

  function dtf(lang, tz, opts) {
    try {
      return new Intl.DateTimeFormat(lang, Object.assign({ timeZone: tz }, opts));
    } catch (e) {
      return new Intl.DateTimeFormat(undefined, opts);
    }
  }

  /* sensor.boost_end.last_error: known codes are translated, free text is shown as is. */
  function lastErrorText(lang, code) {
    const c = String(code);
    if (/^[a-z0-9_]+$/.test(c)) {
      if (/deadline|timeout|time_limit/.test(c)) return tr(lang, 'le_deadline');
      if (c === 'charging_blocked') return tr(lang, 'le_charging_blocked');
      if (c === 'stop_failed') return tr(lang, 'le_stop_failed');
      return tr(lang, 'le_generic', { e: c });
    }
    return c;
  }

  /* --------------------------------------------------------- entity lookup */

  function buildMap(hass, deviceId) {
    const map = {};
    const ents = hass && hass.entities;
    if (!ents || !deviceId) return map;
    for (const id in ents) {
      const e = ents[id];
      if (!e || e.device_id !== deviceId || !e.translation_key) continue;
      if (e.platform && e.platform !== DOMAIN) continue;
      const dot = id.indexOf('.');
      if (dot < 1) continue;
      const k = id.slice(0, dot) + '.' + e.translation_key;
      if (!map[k]) map[k] = id;
    }
    return map;
  }

  function firstDevice(hass) {
    if (!hass) return undefined;
    const ents = hass.entities || {};
    const ids = Object.keys(ents).sort();
    for (const id of ids) {
      const e = ents[id];
      if (e && e.platform === DOMAIN && e.device_id) return e.device_id;
    }
    const devs = hass.devices || {};
    for (const id of Object.keys(devs)) {
      const d = devs[id];
      if (d && Array.isArray(d.identifiers) && d.identifiers.some((x) => Array.isArray(x) && x[0] === DOMAIN)) return id;
    }
    return undefined;
  }

  /* ----------------------------------------------------------------- model */

  function computeModel(hass, map, now) {
    const states = (hass && hass.states) || {};
    const S = (k) => (map[k] ? states[map[k]] : undefined);
    const A = (k) => {
      const s = S(k);
      return (s && s.attributes) || {};
    };
    const N = (k) => {
      const s = S(k);
      if (!s) return null;
      const v = parseFloat(s.state);
      return Number.isFinite(v) ? v : null;
    };
    const tz = tzOf(hass);
    const m = { now, tz };

    const stObj = S('sensor.status');
    m.hasStatus = !!stObj;
    let status = 'unknown';
    if (stObj) {
      if (stObj.state === 'unavailable') status = 'offline';
      else if (STATUSES.includes(stObj.state)) status = stObj.state;
    }
    m.status = status;
    m.statusSince = stObj ? Date.parse(stObj.last_changed) : NaN;
    m.cat = status === 'charging' ? 'charging'
      : FAULTS.has(status) ? 'fault'
        : status === 'offline' ? 'offline'
          : status === 'unknown' ? 'unknown' : 'idle';
    m.charging = status === 'charging';
    m.offline = status === 'offline';

    // electrical values
    m.voltage = N('sensor.voltage');
    m.measured = N('sensor.measured_current');
    m.configured = N('sensor.configured_current');
    let kw = N('sensor.power');
    if (kw !== null) {
      const u = String(A('sensor.power').unit_of_measurement || 'W');
      if (u === 'W') kw /= 1000;
      else if (u === 'MW') kw *= 1000;
    }
    if (kw === null && m.charging && m.measured !== null) kw = (m.measured * (m.voltage || 230)) / 1000;
    m.kw = kw === null ? 0 : Math.max(0, kw);

    // amps
    const aS = S('number.charging_amps');
    const aA = A('number.charging_amps');
    m.hasAmps = !!aS;
    m.ampsMin = finite(aA.min, 6);
    m.ampsMax = finite(aA.max, 32);
    if (m.ampsMax <= m.ampsMin) m.ampsMax = m.ampsMin + 1;
    m.ampsStep = finite(aA.step, 2) || 2;
    const ampsVal = N('number.charging_amps');
    m.amps = ampsVal !== null ? ampsVal : m.configured;
    m.ampsAvail = !!aS && aS.state !== 'unavailable';

    // energy
    m.hasSession = !!S('sensor.session_energy');
    m.session = N('sensor.session_energy');
    const sA = A('sensor.session_energy');
    m.startedAt = sA.started_at ? Date.parse(sA.started_at) : NaN;
    m.history = Array.isArray(sA.history)
      ? sA.history.filter((x) => x && typeof x === 'object').slice(0, 50)
      : [];
    m.sessionCost = finite(sA.cost, null);
    m.hasMonth = !!S('sensor.month_energy');
    m.month = N('sensor.month_energy');
    const mA = A('sensor.month_energy');
    m.months = Array.isArray(mA.months) ? mA.months : null;
    m.curMonth = finite(mA.current_month, null);

    // two-zone tariff (backend-owned prices; the card never computes them itself)
    m.priceDay = N('number.price_day');
    m.priceNight = N('number.price_night');
    m.priceNow = N('sensor.current_price');

    // boost
    const bS = S('sensor.boost_end');
    const bA = A('sensor.boost_end');
    const endMs = bS ? Date.parse(bS.state) : NaN;
    m.boostEnd = Number.isFinite(endMs) ? endMs : null;
    m.boostMinutes = finite(bA.minutes, 0);
    m.boostMode = typeof bA.mode === 'string' ? bA.mode : null;
    // energy boost: boost_end state is the ESTIMATED end (unknown while power is 0)
    m.boostEnergy = m.boostMode === 'energy' && bA.active !== false;
    m.targetKwh = finite(bA.target_kwh, null);
    m.deliveredKwh = Math.max(0, finite(bA.delivered_kwh, 0));
    const dl = typeof bA.deadline === 'string' ? Date.parse(bA.deadline) : NaN;
    m.deadline = Number.isFinite(dl) ? dl : null;
    m.etaMs = m.boostEnergy ? m.boostEnd : null;
    m.lastError = typeof bA.last_error === 'string' && bA.last_error.trim() ? bA.last_error.trim() : null;
    m.boostUnlimited = !m.boostEnergy && (bA.unlimited === true || m.boostMode === 'unlimited') && bA.active !== false;
    // active === true keeps the countdown at 0:00 until the backend actually stops (no flicker to "no limit")
    m.boostTimed = !m.boostEnergy && !m.boostUnlimited && m.boostEnd !== null && bA.active !== false
      && (bA.active === true || m.boostEnd > now - 1000);
    m.boostActive = m.boostTimed || m.boostUnlimited || m.boostEnergy;
    m.boostSetting = N('number.boost_minutes');
    m.boostKwhSetting = N('number.boost_kwh');
    // the backend supports electros_ev.charge_energy when it exposes its entities
    m.hasEnergy = !!(map['number.boost_kwh'] || map['button.boost_energy']) || m.boostEnergy;

    // switches
    m.hasChargeSw = !!S('switch.charging');
    m.hasNight = !!S('switch.night_timer');
    m.nightOn = !!S('switch.night_timer') && S('switch.night_timer').state === 'on';
    m.hasWeekend = !!S('switch.weekend_free');
    m.weekendOn = !!S('switch.weekend_free') && S('switch.weekend_free').state === 'on';

    // night window
    m.hasStart = !!S('time.timer_start');
    m.hasEnd = !!S('time.timer_end');
    const ts = parseHM(S('time.timer_start') && S('time.timer_start').state);
    const te = parseHM(S('time.timer_end') && S('time.timer_end').state);
    m.tStart = ts === null ? DEFAULT_START : ts;
    m.tEnd = te === null ? DEFAULT_END : te;
    const p = zoneParts(now, tz);
    m.nowMin = p.h * 60 + p.mi + p.s / 60;
    m.nowMonth = p.mo;
    m.weekendToday = p.wd === 'Sat' || p.wd === 'Sun';
    derive(m);
    return m;
  }

  /* Values that depend on the (possibly optimistic) night settings. */
  function derive(m) {
    m.timerEffective = m.nightOn && !(m.weekendOn && m.weekendToday);
    m.inWin = inWindow(m.nowMin, m.tStart, m.tEnd);
    m.waitingNight = m.timerEffective && !m.inWin;
    m.minsToStart = m.waitingNight ? Math.ceil(mod1440(m.tStart - m.nowMin)) : 0;
    m.minsToEnd = m.inWin ? Math.ceil(mod1440(m.tEnd - m.nowMin)) : 0;
    m.winLen = mod1440(m.tEnd - m.tStart) || 1440;
    m.remainingKwh = m.targetKwh !== null ? Math.max(0, m.targetKwh - m.deliveredKwh) : null;
    if (m.charging) {
      if (m.boostEnergy) {
        m.mode = 'energy';
        m.frac = m.targetKwh > 0 ? clamp(m.deliveredKwh / m.targetKwh, 0, 1) : 0;
      } else if (m.boostTimed) {
        m.mode = 'timed';
        m.remaining = Math.max(0, (m.boostEnd - m.now) / 1000);
        const total = m.boostMinutes > 0 ? m.boostMinutes * 60 : Math.max(m.remaining, 1);
        m.frac = clamp(m.remaining / total, 0, 1);
      } else if (m.boostUnlimited) {
        m.mode = 'unlimited';
        m.frac = 1;
      } else if (m.timerEffective && m.inWin) {
        m.mode = 'schedule';
        m.frac = clamp(m.minsToEnd / m.winLen, 0, 1);
      } else {
        m.mode = 'free';
        m.frac = 1;
      }
    } else {
      m.mode = 'none';
      m.frac = 0;
    }
    // Starting now would override the night timer -> deliberate "slide to charge" + hint.
    m.bypass = !m.charging && m.cat === 'idle' && (m.status === 'charging_blocked' || m.waitingNight);
    m.heroTime = !m.charging && m.waitingNight
      && (m.status === 'vehicle_connected_idle' || m.status === 'charging_blocked');
    return m;
  }

  /* Pill (status chip) shared by card and tile. */
  function pillFor(m, lang) {
    const s = m.status;
    if (s === 'charging') return { kind: 'green', icon: null, pulse: true, text: tr(lang, 'st_charging') };
    if (m.cat === 'fault') return { kind: 'red', icon: null, text: tr(lang, 'st_' + s) };
    if (s === 'offline' || s === 'unknown') return { kind: 'neutral', icon: null, text: tr(lang, 'st_' + s) };
    if (s === 'charging_blocked') return { kind: 'purple', icon: m.nightOn ? 'moon' : 'lock', text: tr(lang, 'st_charging_blocked') };
    if (s === 'vehicle_connected_idle' && m.waitingNight) return { kind: 'purple', icon: 'moon', text: tr(lang, 'waiting_night') };
    return { kind: 'neutral', icon: null, text: tr(lang, 'st_' + s) };
  }

  function normalizeKwhList(raw) {
    let q = raw;
    if (typeof q === 'string') q = q.split(/[,;\s]+/);
    if (!Array.isArray(q)) return DEFAULT_QUICK_KWH.slice();
    q = Array.from(new Set(q.map((x) => (x === null || x === '' ? NaN : Math.round(Number(x) * 10) / 10))
      .filter((x) => Number.isFinite(x) && x >= KWH_MIN && x <= KWH_MAX)))
      .sort((a, b) => a - b)
      .slice(0, 5);
    return q.length ? q : DEFAULT_QUICK_KWH.slice();
  }

  function normalizeConfig(c, tile) {
    const src = c && typeof c === 'object' ? c : {};
    const out = Object.assign({}, src);
    let q = src.quick_minutes;
    if (typeof q === 'string') q = q.split(/[,;\s]+/);
    if (!Array.isArray(q)) q = DEFAULT_QUICK;
    q = Array.from(new Set(q.map((x) => Math.round(Number(x))).filter((x) => Number.isFinite(x) && x >= 1 && x <= 1440)))
      .sort((a, b) => a - b)
      .slice(0, 5);
    if (!q.length) q = DEFAULT_QUICK.slice();
    out.quick_minutes = q;
    out.quick_kwh = normalizeKwhList(src.quick_kwh);
    out.theme = ['light', 'dark', 'auto'].includes(src.theme) ? src.theme : 'auto';
    // price_per_kwh (v2.0 card option) is ignored: cost now comes from the backend tariff sensors
    delete out.price_per_kwh;
    // YAML-only: initial statistics period and charge mode
    out.stats_period = PERIODS.includes(src.stats_period) ? src.stats_period : 'week';
    out.charge_mode = src.charge_mode === 'energy' ? 'energy' : 'time';
    out.device_id = typeof src.device_id === 'string' && src.device_id ? src.device_id : null;
    if (tile) out.size = src.size === 'small' ? 'small' : 'medium';
    return out;
  }

  /* ------------------------------------------------------------ DOM helpers */

  const ICON_PATHS = {
    bolt: '<path d="M13 3 5 14h6l-1 7 8-11h-6z"/>',
    timer: '<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 2M9.5 2.5h5"/>',
    clock: '<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 2"/>',
    moon: '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
    plug: '<path d="M9 2v5M15 2v5M6 7h12v4a6 6 0 0 1-12 0zM12 17v5"/>',
    lock: '<rect x="5" y="11" width="14" height="10" rx="2.5"/><path d="M8.5 11V8a3.5 3.5 0 0 1 7 0v3"/>',
    warn: '<path d="M10.3 3.9 2.4 17.5a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9.5v4M12 17h.01"/>',
    offline: '<path d="M3 3l18 18M2 8.5a15 15 0 0 1 4.3-2.7M10.6 5.1A15 15 0 0 1 22 8.5M5.2 12a10 10 0 0 1 3.4-2M15.6 10.6a10 10 0 0 1 3.2 1.4M8.6 15.4a5 5 0 0 1 5.6-.6M12 19.5h.01"/>',
    question: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.3a2.6 2.6 0 0 1 5 .9c0 1.7-2.5 2.2-2.5 3.8M12 17h.01"/>',
    stop: '<path d="M7 7h10v10H7z"/>',
    play: '<path d="M8 5v14l11-7z"/>',
    restore: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5"/>',
    chev: '<path d="m9 6 6 6-6 6"/>',
    chevl: '<path d="m15 6-6 6 6 6"/>',
    battery: '<rect x="2.5" y="7" width="16.5" height="10" rx="2.5"/><path d="M21.5 10.5v3M10.8 9.2l-2.3 3.3h3.3l-2.3 3.3"/>',
    chart: '<path d="M4 20V10M10 20V4M16 20v-7M21 20H3"/>',
    gear: '<path d="M10.3 4.3c.4-1.7 3-1.7 3.4 0a1.7 1.7 0 0 0 2.6 1.1c1.5-.9 3.3.8 2.4 2.4a1.7 1.7 0 0 0 1 2.5c1.8.4 1.8 3 0 3.4a1.7 1.7 0 0 0-1 2.6c.9 1.5-.9 3.3-2.4 2.4a1.7 1.7 0 0 0-2.6 1c-.4 1.8-3 1.8-3.4 0a1.7 1.7 0 0 0-2.5-1c-1.6.9-3.3-.9-2.4-2.4a1.7 1.7 0 0 0-1.1-2.6c-1.7-.4-1.7-3 0-3.4a1.7 1.7 0 0 0 1.1-2.5c-.9-1.6.8-3.3 2.4-2.4a1.7 1.7 0 0 0 2.5-1.1z"/><circle cx="12" cy="12" r="3"/>',
  };

  /* Static, trusted markup only (icon names come from the code, never from entities). */
  function svgIcon(name, size, sw) {
    return '<svg width="' + size + '" height="' + size + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="'
      + (sw || 2) + '" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">'
      + (ICON_PATHS[name] || '') + '</svg>';
  }

  function setText(el, v) {
    const s = v === null || v === undefined ? '' : String(v);
    if (el.__t !== s) {
      el.textContent = s;
      el.__t = s;
    }
  }

  function setAttr(el, k, v) {
    const key = '__a_' + k;
    if (v === null || v === undefined || v === false) {
      if (el[key] !== null) {
        el.removeAttribute(k);
        el[key] = null;
      }
      return;
    }
    const s = String(v);
    if (el[key] !== s) {
      el.setAttribute(k, s);
      el[key] = s;
    }
  }

  function show(el, on) {
    const v = !!on;
    if (el.__h !== v) {
      // attribute, not the .hidden property: SVG elements have no .hidden IDL attribute
      if (v) el.removeAttribute('hidden');
      else el.setAttribute('hidden', '');
      el.__h = v;
    }
  }

  function setClass(el, c) {
    if (el.__c !== c) {
      el.setAttribute('class', c);
      el.__c = c;
    }
  }

  function css(el, prop, v) {
    const key = '__s_' + prop;
    if (el[key] !== v) {
      el.style.setProperty(prop, v);
      el[key] = v;
    }
  }

  function setDisabled(el, d) {
    const v = !!d;
    if (el.__d !== v) {
      el.disabled = v;
      el.__d = v;
    }
  }

  function setIcon(el, name, size, sw) {
    const key = name + '|' + size + '|' + (sw || 2);
    if (el.__i !== key) {
      el.innerHTML = name ? svgIcon(name, size, sw) : '';
      el.__i = key;
    }
  }

  function collectRefs(root) {
    const r = {};
    root.querySelectorAll('[data-r]').forEach((el) => {
      r[el.getAttribute('data-r')] = el;
    });
    return r;
  }

  function fire(node, type, detail) {
    node.dispatchEvent(new CustomEvent(type, { detail, bubbles: true, composed: true }));
  }

  let UID = 0;

  /* ---------------------------------------------------------------- styles */

  const THEME_CSS = `
:host{display:block}
*{box-sizing:border-box}
[hidden]{display:none!important}
ha-card{display:block;position:relative;overflow:hidden;isolation:isolate;border:none;
  border-radius:var(--es-radius,28px);background:var(--bg);color:var(--fg);box-shadow:none;
  font-family:-apple-system,"SF Pro Text",Inter,system-ui,sans-serif;-webkit-font-smoothing:antialiased;
  font-size:15px;line-height:1.25;letter-spacing:normal;text-align:left}
button{font-family:inherit;cursor:pointer;-webkit-tap-highlight-color:transparent;margin:0}
button:disabled{cursor:default;opacity:.45}
button:focus-visible,input:focus-visible,[tabindex]:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
.num{font-variant-numeric:tabular-nums}
.dark{color-scheme:dark;
  --bg:#07090C;--fg:#F5F5F7;--fg2:rgba(235,235,245,.6);--fg3:rgba(235,235,245,.45);
  --glass:rgba(255,255,255,.07);--glass-b:rgba(255,255,255,.10);
  --tile:rgba(255,255,255,.06);--tile-b:rgba(255,255,255,.08);
  --btn:rgba(255,255,255,.08);--btn-b:rgba(255,255,255,.12);
  --chip:rgba(255,255,255,.06);--chip-b:rgba(255,255,255,.10);--chip-on:#F5F5F7;--chip-on-fg:#0B0D10;
  --accent:#30D158;--accent-fg:#04210E;--grad-a:#64D2FF;
  --blue:#64D2FF;--blue-bg:rgba(100,210,255,.16);
  --green:#4CE07A;--green-bg:rgba(48,209,88,.16);--green-dot:#30D158;
  --red:#FF6961;--red-bg:rgba(255,69,58,.16);--red-b:rgba(255,69,58,.35);--red-ring:rgba(255,69,58,.45);
  --orange:#FFB340;--orange-bg:rgba(255,159,10,.12);
  --purple:#A5A3FF;--purple-bg:rgba(94,92,230,.22);--purple-pill:#A5A3FF;--purple-pill-bg:rgba(94,92,230,.22);--purple-dash:rgba(125,122,255,.45);
  --seg:linear-gradient(90deg,#5E5CE6,#7D7AFF);--seg2:linear-gradient(90deg,#7D7AFF,#5E5CE6);--seg-off:rgba(255,255,255,.10);
  --tl:rgba(255,255,255,.06);--now:#FFFFFF;--sw-off:rgba(120,120,128,.32);
  --neutral-bg:rgba(255,255,255,.10);--neutral:rgba(235,235,245,.75);--neutral-dot:rgba(235,235,245,.6);
  --ring-track:rgba(255,255,255,.07);--grey-dash:rgba(235,235,245,.22);
  --sl-track:rgba(255,255,255,.08);--tick-on:rgba(4,33,14,.35);--tick-off:rgba(255,255,255,.18);
  --bar:rgba(255,255,255,.14);--sep:rgba(84,84,88,.55);
  --slide:rgba(48,209,88,.14);--slide-fill:rgba(48,209,88,.24);--slide-knob:#30D158;--slide-knob-fg:#04210E;--slide-fg:#4CE07A;
  --glow-on:rgba(48,209,88,.33);--glow-idle:rgba(120,120,128,.22);--glow-night:rgba(94,92,230,.30);--glow-fault:rgba(255,69,58,.26);--glow-off:rgba(120,120,128,.14);
  --focus:#64D2FF;--thumb-b:transparent;--muted-ic:rgba(235,235,245,.45);--tile-base:#141619;--input:rgba(255,255,255,.08);
  --c-day:#D97706;--c-night:#5E5CE6;--c-fb:rgba(235,235,245,.30);--c-fb2:rgba(235,235,245,.12);--grid:rgba(255,255,255,.09);
  --segc:rgba(118,118,128,.24);--segc-on:rgba(255,255,255,.17);--segc-sh:inset 0 .5px 0 rgba(255,255,255,.22),0 2px 8px rgba(0,0,0,.28)}
.light{color-scheme:light;
  --bg:#F2F2F7;--fg:#1C1C1E;--fg2:rgba(60,60,67,.6);--fg3:rgba(60,60,67,.45);
  --glass:rgba(255,255,255,.78);--glass-b:rgba(0,0,0,.05);
  --tile:rgba(255,255,255,.72);--tile-b:rgba(0,0,0,.05);
  --btn:rgba(255,255,255,.8);--btn-b:rgba(0,0,0,.06);
  --chip:rgba(118,118,128,.12);--chip-b:transparent;--chip-on:#1C1C1E;--chip-on-fg:#FFFFFF;
  --accent:#34C759;--accent-fg:#FFFFFF;--grad-a:#5AC8FA;
  --blue:#007AFF;--blue-bg:rgba(0,122,255,.12);
  --green:#248A3D;--green-bg:rgba(52,199,89,.14);--green-dot:#34C759;
  --red:#D70015;--red-bg:rgba(255,59,48,.12);--red-b:rgba(255,59,48,.30);--red-ring:rgba(255,59,48,.35);
  --orange:#C93400;--orange-bg:rgba(255,149,0,.14);
  --purple:#5856D6;--purple-bg:rgba(88,86,214,.14);--purple-pill:#4745B8;--purple-pill-bg:rgba(88,86,214,.12);--purple-dash:rgba(88,86,214,.35);
  --seg:linear-gradient(90deg,#5856D6,#7D7AFF);--seg2:linear-gradient(90deg,#7D7AFF,#5856D6);--seg-off:rgba(118,118,128,.16);
  --tl:rgba(118,118,128,.12);--now:#1C1C1E;--sw-off:rgba(120,120,128,.16);
  --neutral-bg:rgba(118,118,128,.12);--neutral:rgba(60,60,67,.75);--neutral-dot:rgba(60,60,67,.5);
  --ring-track:rgba(0,0,0,.06);--grey-dash:rgba(60,60,67,.18);
  --sl-track:rgba(118,118,128,.14);--tick-on:rgba(255,255,255,.6);--tick-off:rgba(60,60,67,.18);
  --bar:rgba(118,118,128,.16);--sep:rgba(60,60,67,.18);
  --slide:rgba(52,199,89,.12);--slide-fill:rgba(52,199,89,.22);--slide-knob:#34C759;--slide-knob-fg:#FFFFFF;--slide-fg:#248A3D;
  --glow-on:rgba(52,199,89,.20);--glow-idle:rgba(88,86,214,.10);--glow-night:rgba(88,86,214,.16);--glow-fault:rgba(255,59,48,.14);--glow-off:rgba(142,142,147,.14);
  --focus:#007AFF;--thumb-b:rgba(0,0,0,.08);--muted-ic:rgba(60,60,67,.45);--tile-base:#FCFCFE;--input:rgba(118,118,128,.12);
  --c-day:#D97706;--c-night:#5856D6;--c-fb:rgba(60,60,67,.30);--c-fb2:rgba(60,60,67,.10);--grid:rgba(60,60,67,.12);
  --segc:rgba(118,118,128,.12);--segc-on:#FFFFFF;--segc-sh:0 2px 6px rgba(0,0,0,.10),0 0 0 .5px rgba(0,0,0,.04)}
.ic{display:inline-flex;align-items:center;justify-content:center;flex:none}
.ic.green{color:var(--green-dot)}.ic.purple{color:var(--purple)}.ic.red{color:var(--red)}
.ic.muted{color:var(--muted-ic)}.ic.blue{color:var(--blue)}.ic.grey{color:#8E8E93}
.pulse{animation:pulse 1.8s ease-out infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(48,209,88,.55)}70%{box-shadow:0 0 0 9px rgba(48,209,88,0)}100%{box-shadow:0 0 0 0 rgba(48,209,88,0)}}
@keyframes breathe{0%,100%{opacity:.85}50%{opacity:1}}
@keyframes nudge{0%,100%{transform:translateX(0)}30%{transform:translateX(18px)}60%{transform:translateX(-4px)}}
.empty{position:relative;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;
  text-align:center;padding:36px 24px;min-height:160px}
.empty .ic{width:52px;height:52px;border-radius:18px;background:var(--glass);border:1px solid var(--glass-b);color:var(--fg2)}
.empty h2{margin:6px 0 0;font-size:20px;font-weight:600}
.empty p{margin:0;font-size:14px;line-height:19px;color:var(--fg2);max-width:300px}
@media (prefers-reduced-motion: reduce){
  *,*::before,*::after{animation:none!important;transition:none!important}
}
`;

  const CARD_CSS = THEME_CSS + `
.glow{position:absolute;left:-120px;right:-120px;top:-60px;height:640px;pointer-events:none;z-index:0;
  background:radial-gradient(closest-side,var(--glow-c,var(--glow-idle)),transparent 72%);transition:opacity .6s}
.glow.on{--glow-c:var(--glow-on);animation:breathe 3.2s ease-in-out infinite}
.glow.idle{--glow-c:var(--glow-idle)}.glow.night{--glow-c:var(--glow-night)}
.glow.fault{--glow-c:var(--glow-fault)}.glow.off{--glow-c:var(--glow-off)}
.wrap{position:relative;z-index:1;padding:22px 16px 20px;display:flex;flex-direction:column;gap:14px}
.head{display:flex;align-items:flex-end;justify-content:space-between;gap:12px;padding:0 4px 6px}
.titles{display:flex;flex-direction:column;gap:2px;min-width:0}
.titles .sub{font-size:15px;color:var(--fg2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.titles h1{margin:0;font-size:34px;line-height:40px;font-weight:700;letter-spacing:-.4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.round{flex:none;width:44px;height:44px;border-radius:22px;border:1px solid var(--btn-b);background:var(--btn);color:var(--fg);
  -webkit-backdrop-filter:blur(20px);backdrop-filter:blur(20px);display:flex;align-items:center;justify-content:center;padding:0}
.hero{display:flex;flex-direction:column;align-items:center;gap:14px;padding:6px 0 2px}
.pill{display:inline-flex;align-items:center;gap:8px;height:32px;padding:0 14px;border-radius:16px;font-size:15px;font-weight:600;max-width:100%}
.pill>span:last-child{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pill .dot{flex:none;width:8px;height:8px;border-radius:4px;background:var(--neutral-dot)}
.pill.green{background:var(--green-bg);color:var(--green)}.pill.green .dot{background:var(--green-dot)}
.pill.purple{background:var(--purple-pill-bg);color:var(--purple-pill)}
.pill.red{background:var(--red-bg);color:var(--red)}.pill.red .dot{background:var(--red)}
.pill.neutral{background:var(--neutral-bg);color:var(--neutral)}
.ring{position:relative;width:272px;max-width:100%;aspect-ratio:1/1}
.ring svg.r{position:absolute;inset:0;width:100%;height:100%;overflow:visible}
.r-track{stroke:var(--ring-track);transition:stroke .4s}
.ring.fault .r-track{stroke:var(--red-ring)}
.r-dash{stroke:var(--grey-dash)}.ring.night .r-dash{stroke:var(--purple-dash)}
.r-prog{transition:stroke-dashoffset .6s ease}
.ring-in{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;padding:0 34px;text-align:center}
.ring-val{display:flex;align-items:baseline;justify-content:center;gap:6px;max-width:100%}
.ring-val .v{font-size:68px;line-height:72px;font-weight:600;letter-spacing:-2px;font-variant-numeric:tabular-nums}
.ring-val .u{font-size:20px;font-weight:500;color:var(--fg2)}
.ring.t-time .ring-val .v{font-size:48px;line-height:54px;letter-spacing:-1.4px}
.ring.t-time .ring-in{gap:4px}
.ring.t-msg .ring-val .v{font-size:28px;line-height:32px;letter-spacing:-.5px;white-space:normal}
.ring.t-msg .ring-in{gap:6px}
.ring-sub{font-size:15px;color:var(--fg2);font-variant-numeric:tabular-nums}
.glass{border-radius:28px;background:var(--glass);border:1px solid var(--glass-b);
  -webkit-backdrop-filter:blur(30px) saturate(180%);backdrop-filter:blur(30px) saturate(180%)}
.count{display:flex;align-items:center;gap:14px;padding:12px 12px 12px 18px;border-radius:30px}
.count .txt{display:flex;flex-direction:column;flex:1;min-width:0}
.count .big{font-size:26px;line-height:30px;font-weight:600;font-variant-numeric:tabular-nums;letter-spacing:-.5px}
.count .small{font-size:13px;color:var(--fg2)}
.plus{flex:none;height:44px;padding:0 16px;border-radius:22px;border:0;background:var(--blue-bg);color:var(--blue);font-size:15px;font-weight:600}
.hint{display:flex;gap:12px;align-items:flex-start;padding:14px 16px;border-radius:22px}
.hint .ic{width:32px;height:32px;border-radius:10px;background:var(--green-bg);color:var(--green)}
.hint .txt{display:flex;flex-direction:column;gap:2px}
.hint b{font-size:15px;font-weight:600}
.hint span.t{font-size:13px;line-height:18px;color:var(--fg2)}
.stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.stat{padding:14px;border-radius:22px;background:var(--tile);border:1px solid var(--tile-b);display:flex;flex-direction:column;gap:4px;min-width:0}
.stat .l{font-size:12px;color:var(--fg2)}
.stat .v{font-size:20px;font-weight:600;font-variant-numeric:tabular-nums;display:flex;flex-wrap:wrap;align-items:baseline;column-gap:4px;min-width:0}
.stat .v>span:first-child{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}
.stat .u{font-size:13px;font-weight:500;color:var(--fg2);white-space:nowrap}
.panel{padding:18px;display:flex;flex-direction:column;gap:14px}
.panel.amps{gap:12px}
.ph{display:flex;align-items:baseline;justify-content:space-between;gap:12px}
.ph h2{margin:0;font-size:20px;line-height:24px;font-weight:600;white-space:nowrap}
.ph .s{font-size:13px;color:var(--fg2);text-align:right;font-variant-numeric:tabular-nums}
.ph .s.kw{font-size:15px}
.chips{display:flex;gap:8px}
.chip{flex:1 1 0;min-width:0;height:44px;border-radius:22px;font-size:15px;font-weight:600;padding:0 4px;
  border:1px solid var(--chip-b);background:var(--chip);color:var(--fg);white-space:nowrap;transition:background .15s,color .15s}
.chip[aria-pressed="true"]{border-color:transparent;background:var(--chip-on);color:var(--chip-on-fg)}
.main{height:56px;border-radius:28px;display:flex;align-items:center;justify-content:center;gap:8px;font-size:17px;font-weight:600;
  border:0;background:var(--accent);color:var(--accent-fg);padding:0 16px}
.main.stop{border:1px solid var(--red-b);background:var(--red-bg);color:var(--red)}
.main.neutral{border:1px solid var(--chip-b);background:var(--chip);color:var(--fg)}
.slide{position:relative;height:64px;border-radius:32px;background:var(--slide);display:flex;align-items:center;overflow:hidden;touch-action:pan-y;user-select:none;-webkit-user-select:none}
.slide .fill{position:absolute;left:0;top:0;bottom:0;width:64px;border-radius:32px;background:var(--slide-fill);transition:width .25s ease}
.slide .knob{position:absolute;left:4px;top:4px;width:56px;height:56px;border-radius:28px;border:0;background:var(--slide-knob);color:var(--slide-knob-fg);
  display:flex;align-items:center;justify-content:center;padding:0;touch-action:none;transition:transform .25s ease;z-index:2;box-shadow:0 2px 8px rgba(0,0,0,.18)}
.slide.drag .fill,.slide.drag .knob{transition:none}
.slide .knob.nudge{animation:nudge .6s ease}
.slide .lbl{position:relative;flex:1;text-align:center;font-size:16px;line-height:19px;font-weight:600;color:var(--slide-fg);padding:0 40px 0 66px;
  overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;pointer-events:none;transition:opacity .2s}
.slide .chev{position:absolute;right:20px;top:21px;color:var(--slide-fg);opacity:.6;pointer-events:none}
.slide.dis{opacity:.45}
.amps-big{font-size:44px;line-height:48px;font-weight:600;letter-spacing:-1px;font-variant-numeric:tabular-nums}
.amps-big .u{font-size:22px;font-weight:500;color:var(--fg2)}
.slider{position:relative;height:44px}
.sl-track{position:absolute;left:0;right:0;top:14px;height:16px;border-radius:8px;background:var(--sl-track);overflow:hidden}
.sl-fill{height:100%;border-radius:8px;background:linear-gradient(90deg,var(--grad-a),var(--accent))}
.sl-ticks{position:absolute;left:13px;right:13px;top:19px;height:6px;display:flex;justify-content:space-between;pointer-events:none}
.sl-ticks span{width:2px;height:6px;border-radius:1px;background:var(--tick-off)}
.sl-ticks span.on{background:var(--tick-on)}
.sl-thumb{position:absolute;top:8px;width:28px;height:28px;border-radius:14px;background:#FFFFFF;border:.5px solid var(--thumb-b);
  box-shadow:0 1px 5px rgba(0,0,0,.22);pointer-events:none}
.sl-in{position:absolute;inset:0;width:100%;height:44px;margin:0;opacity:0;cursor:pointer;-webkit-appearance:none;appearance:none;background:transparent}
.sl-in::-webkit-slider-thumb{-webkit-appearance:none;width:28px;height:44px;cursor:pointer}
.sl-in::-moz-range-thumb{width:28px;height:44px;border:0;background:transparent;cursor:pointer}
.sl-in:disabled{cursor:default}
.slider:has(.sl-in:focus-visible) .sl-thumb{outline:2px solid var(--focus);outline-offset:2px}
.slider.dis{opacity:.45}
.sl-lbl{position:relative;height:16px;font-size:12px;color:var(--fg3);font-variant-numeric:tabular-nums}
.sl-lbl span{position:absolute;top:0;white-space:nowrap}
.sl-lbl .mn{left:0}.sl-lbl .mx{right:0}.sl-lbl .md{transform:translateX(-50%)}
.nh{display:flex;align-items:center;justify-content:space-between;gap:12px}
.nh .l{display:flex;align-items:center;gap:12px;min-width:0}
.sq{flex:none;width:36px;height:36px;border-radius:12px;background:var(--purple-bg);color:var(--purple);display:flex;align-items:center;justify-content:center}
.nh .t{display:flex;flex-direction:column;min-width:0}
.nh h2{margin:0;font-size:17px;line-height:22px;font-weight:600}
.nline{position:relative;background:none;border:0;padding:0;margin:0;color:var(--fg2);font-size:13px;line-height:18px;text-align:left;
  font-variant-numeric:tabular-nums;text-decoration:underline dotted var(--fg3);text-underline-offset:3px}
.nline::after{content:"";position:absolute;left:-6px;right:-6px;top:-13px;bottom:-13px}
.nline:disabled{opacity:1;text-decoration:none}
.sw{position:relative;flex:none;width:51px;height:31px;border-radius:16px;border:0;padding:0;background:var(--sw-off);transition:background .2s}
.sw::after{content:"";position:absolute;left:-4px;right:-4px;top:-7px;bottom:-7px}
.sw[aria-checked="true"]{background:var(--accent)}
.sw .k{position:absolute;top:2px;left:2px;width:27px;height:27px;border-radius:14px;background:#FFFFFF;box-shadow:0 2px 4px rgba(0,0,0,.2);transition:left .2s}
.sw[aria-checked="true"] .k{left:22px}
.nedit{display:grid;grid-template-columns:1fr 1fr auto;gap:10px;align-items:end}
.nedit label{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--fg2);min-width:0}
.nedit input{height:44px;border-radius:14px;border:1px solid var(--glass-b);background:var(--input);color:var(--fg);
  font:600 17px/1 -apple-system,"SF Pro Text",Inter,system-ui,sans-serif;font-variant-numeric:tabular-nums;padding:0 10px;width:100%;min-width:0}
.nedit .ok{height:44px;padding:0 16px;border-radius:22px;border:0;background:var(--chip-on);color:var(--chip-on-fg);font-size:15px;font-weight:600}
.tl{display:flex;flex-direction:column;gap:6px}
.tl-bar{position:relative;height:28px;border-radius:10px;background:var(--tl);overflow:hidden}
.tl-seg{position:absolute;top:0;bottom:0;background:var(--seg-off)}
.tl.on .tl-seg.a{background:var(--seg)}.tl.on .tl-seg.b{background:var(--seg2)}
.tl-now{position:absolute;top:3px;bottom:3px;width:3px;margin-left:-1.5px;border-radius:2px;background:var(--now)}
.tl-lbl{display:flex;justify-content:space-between;font-size:11px;color:var(--fg3);font-variant-numeric:tabular-nums}
.bypass{display:flex;gap:10px;align-items:flex-start;padding:12px;border-radius:16px;background:var(--orange-bg);color:var(--orange);font-size:13px;line-height:18px}
.bypass .ic{margin-top:1px}
.wk{display:flex;align-items:center;justify-content:space-between;gap:12px;padding-top:12px;border-top:.5px solid var(--sep)}
.wk span{font-size:15px}
.bars{display:flex;align-items:flex-end;gap:10px;height:120px}
.bar{flex:1 1 0;min-width:0;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;gap:6px;height:100%}
.bar .bv{font-size:11px;color:var(--fg2);font-variant-numeric:tabular-nums;white-space:nowrap}
.bar .bb{width:100%;border-radius:8px;background:var(--bar);transition:height .4s ease}
.bar.hi .bb{background:linear-gradient(180deg,var(--grad-a),var(--accent))}
.bars-lbl{display:flex;gap:10px}
.bars-lbl span{flex:1 1 0;min-width:0;text-align:center;font-size:11px;color:var(--fg3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bars-empty{font-size:13px;color:var(--fg2);padding:8px 0}
.sess{display:flex;flex-direction:column;gap:14px}
.segc{display:flex;gap:2px;padding:2px;border-radius:18px;background:var(--segc)}
.segc button{flex:1 1 0;min-width:0;height:32px;border:0;border-radius:16px;background:transparent;color:var(--fg2);font-size:13px;font-weight:600;
  display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:0 6px;white-space:nowrap;transition:background .2s,box-shadow .2s,color .2s}
.segc button[aria-pressed="true"]{background:var(--segc-on);box-shadow:var(--segc-sh);color:var(--fg)}
.segc button>span:last-child{overflow:hidden;text-overflow:ellipsis}
.chip .cu{font-size:12px;font-weight:500;opacity:.72;margin-left:3px}
.count .big{display:flex;align-items:baseline;gap:5px;min-width:0;white-space:nowrap;overflow:hidden}
.count .big .bu{font-size:15px;font-weight:500;letter-spacing:0;color:var(--fg2)}
.berr{display:flex;align-items:flex-start;gap:8px;margin-top:-4px;padding:0 18px;color:var(--red);font-size:13px;line-height:18px}
.berr .ic{margin-top:1px}
.st{display:flex;flex-direction:column;gap:12px}
.snav{display:flex;align-items:center;justify-content:space-between;gap:8px}
.snav .pt{flex:1;min-width:0;text-align:center;font-size:15px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.navb{position:relative;flex:none;width:32px;height:32px;border-radius:16px;border:0;background:var(--chip);color:var(--fg);display:flex;align-items:center;justify-content:center;padding:0}
.navb::after{content:"";position:absolute;inset:-6px}
.chart{position:relative;display:flex;flex-direction:column;gap:6px;border-radius:10px;transition:opacity .2s}
.chart.stale{opacity:.45}
.sread{height:18px;display:flex;align-items:center;gap:6px;font-size:12px;color:var(--fg3);font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden}
.sread b{font-size:13px;font-weight:600;color:var(--fg)}
.sread .lab{color:var(--fg2);overflow:hidden;text-overflow:ellipsis}
.sread .k{display:inline-block;flex:none;width:10px;height:3px;border-radius:2px}
.k.d,.sg.d,.sw2.d{background:var(--c-day)}.k.n,.sg.n,.sw2.n{background:var(--c-night)}.k.f{background:var(--c-fb)}
.plotw{position:relative;height:120px;padding-right:30px}
.sgrid{position:absolute;inset:0;pointer-events:none}
.gl{position:absolute;left:0;right:30px;height:1px;background:var(--grid)}
.yl{position:absolute;right:0;width:27px;text-align:right;font-size:10px;line-height:12px;color:var(--fg3);font-variant-numeric:tabular-nums;transform:translateY(50%)}
.plot{position:relative;height:100%;display:flex;align-items:flex-end;cursor:pointer;touch-action:pan-y}
.col{flex:1 1 0;min-width:0;height:100%;display:flex;align-items:flex-end;justify-content:center;transition:opacity .15s}
.plot.hs .col:not(.on){opacity:.4}
.stk{width:68%;max-width:24px;display:flex;flex-direction:column-reverse;gap:2px;border-radius:4px 4px 0 0;overflow:hidden}
.sg{flex:none;width:100%}
.sg.f,.sw2.f{background:repeating-linear-gradient(135deg,var(--c-fb) 0 3px,var(--c-fb2) 3px 6px)}
.xl{display:flex;height:14px;padding-right:30px}
.xl span{flex:1 1 0;min-width:0;display:flex;justify-content:center;font-size:11px;line-height:14px;color:var(--fg3);white-space:nowrap;font-variant-numeric:tabular-nums}
.xl span.cur{color:var(--fg);font-weight:600}
.sempty{min-height:158px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px;text-align:center;padding:16px;
  border-radius:18px;background:var(--tile);border:1px solid var(--tile-b)}
.sempty .ic{width:36px;height:36px;border-radius:12px;background:var(--purple-bg);color:var(--purple);margin-bottom:2px}
.sempty b{font-size:15px;line-height:20px;font-weight:600;max-width:270px}
.sempty .t{font-size:13px;color:var(--fg2)}
.legend{display:flex;flex-wrap:wrap;gap:8px 16px}
.lg{display:inline-flex;align-items:center;gap:6px;font-size:13px;color:var(--fg2);white-space:nowrap}
.lg .sw2{flex:none;width:10px;height:10px;border-radius:3px}
.lg b{color:var(--fg);font-weight:600;font-variant-numeric:tabular-nums}
.tot{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
.save{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 14px;border-radius:16px;background:var(--green-bg);color:var(--green);font-size:13px;line-height:17px;font-weight:600}
.save b{font-size:17px;font-variant-numeric:tabular-nums;white-space:nowrap}
`;

  const CARD_HTML = (uid) => `
<ha-card data-r="card" class="dark">
  <div class="glow idle" data-r="glow" aria-hidden="true"></div>
  <div class="empty" data-r="empty" hidden>
    <span class="ic">${svgIcon('plug', 26, 1.8)}</span>
    <h2 data-r="emptyTitle"></h2>
    <p data-r="emptyText"></p>
  </div>
  <div class="wrap" data-r="main" hidden>
    <div class="head">
      <div class="titles"><span class="sub" data-r="area"></span><h1 data-r="title">ElectroS</h1></div>
      <button class="round" data-r="settings" type="button">${svgIcon('gear', 22, 1.8)}</button>
    </div>
    <div class="hero">
      <div class="pill neutral" data-r="pill" role="status" aria-live="polite">
        <span class="dot" data-r="pillDot"></span><span class="ic" data-r="pillIcon" hidden></span><span data-r="pillText"></span>
      </div>
      <div class="ring t-big" data-r="ring" role="img">
        <svg class="r" viewBox="0 0 272 272" aria-hidden="true" focusable="false">
          <defs><linearGradient id="${uid}-g" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" style="stop-color:var(--grad-a)"/><stop offset="1" style="stop-color:var(--accent)"/>
          </linearGradient></defs>
          <circle class="r-track" cx="136" cy="136" r="116" fill="none" stroke-width="18"/>
          <circle class="r-dash" data-r="ringDash" cx="136" cy="136" r="116" fill="none" stroke-width="18" stroke-linecap="round" stroke-dasharray="4 18" transform="rotate(-90 136 136)"/>
          <circle class="r-prog" data-r="ringProg" cx="136" cy="136" r="116" fill="none" stroke="url(#${uid}-g)" stroke-width="18" stroke-linecap="round" stroke-dasharray="${RING_C.toFixed(2)}" stroke-dashoffset="${RING_C.toFixed(2)}" transform="rotate(-90 136 136)"/>
          <circle data-r="ringDot" cx="136" cy="20" r="5" fill="#FFFFFF"/>
        </svg>
        <div class="ring-in">
          <span class="ic" data-r="ringIcon"></span>
          <div class="ring-val"><span class="v" data-r="ringValue"></span><span class="u" data-r="ringUnit"></span></div>
          <span class="ring-sub" data-r="ringSub"></span>
        </div>
      </div>
    </div>
    <div class="glass count" data-r="count" hidden>
      <span class="ic blue" data-r="countIcon"></span>
      <div class="txt"><span class="big"><span data-r="countBig"></span><span class="bu" data-r="countUnit" hidden></span></span><span class="small" data-r="countSub"></span></div>
      <button class="plus" data-r="plus" type="button"></button>
    </div>
    <div class="berr" data-r="boostErr" role="alert" hidden><span class="ic">${svgIcon('warn', 16, 2)}</span><span data-r="boostErrText"></span></div>
    <div class="glass hint" data-r="hint" hidden>
      <span class="ic">${svgIcon('bolt', 18, 2)}</span>
      <div class="txt"><b data-r="hintTitle"></b><span class="t" data-r="hintText"></span></div>
    </div>
    <div class="stats" data-r="stats" hidden>
      <div class="stat"><span class="l" data-r="s1l"></span><span class="v"><span data-r="s1v"></span><span class="u" data-r="s1u"></span></span></div>
      <div class="stat"><span class="l" data-r="s2l"></span><span class="v"><span data-r="s2v"></span><span class="u" data-r="s2u"></span></span></div>
      <div class="stat"><span class="l" data-r="s3l"></span><span class="v"><span data-r="s3v"></span><span class="u" data-r="s3u"></span></span></div>
    </div>
    <section class="glass panel" data-r="chargePanel">
      <div class="ph"><h2 data-r="chargeTitle"></h2><span class="s" data-r="chargeSub"></span></div>
      <div class="segc" data-r="modeSeg" role="group" hidden>
        <button type="button" data-r="segTime"><span class="ic">${svgIcon('timer', 15, 2)}</span><span data-r="segTimeT"></span></button>
        <button type="button" data-r="segEnergy"><span class="ic">${svgIcon('bolt', 15, 2)}</span><span data-r="segEnergyT"></span></button>
      </div>
      <div class="chips" data-r="chips" role="group"></div>
      <button class="main" data-r="mainBtn" type="button"><span class="ic" data-r="mainIcon"></span><span data-r="mainLabel"></span></button>
      <div class="slide" data-r="slide" hidden>
        <div class="fill" data-r="slideFill" aria-hidden="true"></div>
        <button class="knob" data-r="slideKnob" type="button">${svgIcon('bolt', 24, 2.2)}</button>
        <span class="lbl" data-r="slideLabel" aria-hidden="true"></span>
        <span class="chev" aria-hidden="true">${svgIcon('chev', 22, 2.2)}</span>
      </div>
    </section>
    <section class="glass panel amps" data-r="ampsPanel" hidden>
      <div class="ph"><h2 data-r="ampsTitle"></h2><span class="s kw" data-r="ampsKw"></span></div>
      <span class="amps-big"><span data-r="ampsVal"></span><span class="u" data-r="ampsUnit"></span></span>
      <div class="slider" data-r="slider">
        <div class="sl-track" aria-hidden="true"><div class="sl-fill" data-r="slFill"></div></div>
        <div class="sl-ticks" data-r="slTicks" aria-hidden="true"></div>
        <div class="sl-thumb" data-r="slThumb" aria-hidden="true"></div>
        <input class="sl-in" data-r="slIn" type="range" min="6" max="32" step="2" value="16">
      </div>
      <div class="sl-lbl" aria-hidden="true"><span class="mn" data-r="slMin"></span><span class="md" data-r="slMid"></span><span class="mx" data-r="slMax"></span></div>
    </section>
    <section class="glass panel" data-r="nightPanel" hidden>
      <div class="nh">
        <div class="l">
          <span class="sq">${svgIcon('moon', 20, 1.9)}</span>
          <div class="t"><h2 data-r="nightTitle"></h2><button class="nline" data-r="nightLine" type="button"></button></div>
        </div>
        <button class="sw" data-r="nightSw" type="button" role="switch" aria-checked="false"><span class="k"></span></button>
      </div>
      <div class="nedit" data-r="nightEdit" hidden>
        <label><span data-r="lblStart"></span><input data-r="tStart" type="time" step="60"></label>
        <label><span data-r="lblEnd"></span><input data-r="tEnd" type="time" step="60"></label>
        <button class="ok" data-r="nightDone" type="button"></button>
      </div>
      <div class="tl" data-r="tl" aria-hidden="true">
        <div class="tl-bar"><div class="tl-seg a" data-r="segA"></div><div class="tl-seg b" data-r="segB"></div><div class="tl-now" data-r="tlNow"></div></div>
        <div class="tl-lbl"><span>00</span><span>06</span><span>12</span><span>18</span><span>24</span></div>
      </div>
      <div class="bypass" data-r="bypass" hidden><span class="ic">${svgIcon('restore', 18, 2)}</span><span data-r="bypassText"></span></div>
      <div class="wk" data-r="wkRow" hidden><span data-r="wkLabel"></span><button class="sw" data-r="wkSw" type="button" role="switch" aria-checked="false"><span class="k"></span></button></div>
    </section>
    <section class="glass panel" data-r="energyPanel" hidden>
      <div class="ph"><h2 data-r="energyTitle"></h2><span class="s" data-r="energySub"></span></div>
      <div class="st" data-r="statBox" hidden>
        <div class="segc" data-r="perSeg" role="group"></div>
        <div class="snav">
          <button class="navb" data-r="perPrev" type="button">${svgIcon('chevl', 18, 2.2)}</button>
          <span class="pt num" data-r="perTitle" aria-live="polite"></span>
          <button class="navb" data-r="perNext" type="button">${svgIcon('chev', 18, 2.2)}</button>
        </div>
        <div class="chart" data-r="chart" tabindex="0" role="group">
          <div class="sread" data-r="statRead" aria-live="polite"></div>
          <div class="plotw"><div class="sgrid" data-r="sgrid" aria-hidden="true"></div><div class="plot" data-r="plot" aria-hidden="true"></div></div>
          <div class="xl" data-r="xl" aria-hidden="true"></div>
        </div>
        <div class="sempty" data-r="statEmpty" hidden><span class="ic">${svgIcon('chart', 20, 2)}</span><b data-r="statEmptyT"></b><span class="t" data-r="statEmptyS"></span></div>
        <div class="legend" data-r="legend"></div>
        <div class="tot" data-r="tot">
          <div class="stat"><span class="l" data-r="totL1"></span><span class="v"><span data-r="totV1"></span><span class="u" data-r="totU1"></span></span></div>
          <div class="stat"><span class="l" data-r="totL2"></span><span class="v"><span data-r="totV2"></span></span></div>
        </div>
        <div class="save" data-r="save" hidden><span data-r="saveL"></span><b data-r="saveV"></b></div>
      </div>
      <div class="sess" data-r="sessBox">
        <div class="bars" data-r="bars" role="img"></div>
        <div class="bars-lbl" data-r="barsLbl" aria-hidden="true"></div>
        <div class="bars-empty" data-r="barsEmpty" hidden></div>
      </div>
    </section>
  </div>
</ha-card>`;

  /* ------------------------------------------------------------- base class */

  class ElectrosBase extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: 'open' });
      this._config = normalizeConfig({}, this._isTile());
      this._hass = null;
      this._map = {};
      this._mapEnts = undefined;
      this._mapDev = undefined;
      this._prev = null;
      this._pending = {};
      this._busy = null;
      this._sel = undefined;
      this._uid = 'es' + (++UID);
      this._r = null;
      this._m = null;
      this._lang = 'uk';
    }

    _isTile() {
      return false;
    }

    connectedCallback() {
      if (!this._timer) {
        this._timer = setInterval(() => this._update(), 1000);
      }
      this._update();
    }

    disconnectedCallback() {
      if (this._timer) clearInterval(this._timer);
      this._timer = null;
    }

    setConfig(config) {
      this._config = normalizeConfig(config, this._isTile());
      this._mapEnts = undefined;
      this._prev = null;
      this._sel = undefined;
      this._update();
    }

    set hass(h) {
      this._hass = h;
      if (this._changed(h)) this._update();
    }

    get hass() {
      return this._hass;
    }

    _resolve() {
      const h = this._hass;
      const ents = h ? h.entities : undefined;
      const dev = this._config.device_id;
      if (this._mapEnts !== ents || this._mapDev !== dev) {
        this._map = buildMap(h, dev);
        this._mapEnts = ents;
        this._mapDev = dev;
      }
      return this._map;
    }

    /* Cheap identity diff: re-render only when one of our entities (or theme/locale) changed. */
    _changed(h) {
      if (!h) return false;
      const map = this._resolve();
      const loc = h.locale || {};
      const arr = [h.entities, h.devices, h.areas, h.themes && h.themes.darkMode, loc.language, loc.number_format,
        h.config && h.config.time_zone, h.config && h.config.currency];
      const states = h.states || {};
      for (const k in map) arr.push(states[map[k]]);
      const prev = this._prev;
      this._prev = arr;
      if (!prev || prev.length !== arr.length) return true;
      for (let i = 0; i < arr.length; i += 1) if (prev[i] !== arr[i]) return true;
      return false;
    }

    _isDark() {
      const t = this._config.theme;
      if (t === 'dark') return true;
      if (t === 'light') return false;
      const h = this._hass;
      if (h && h.themes && typeof h.themes.darkMode === 'boolean') return h.themes.darkMode;
      try {
        return !!(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
      } catch (e) {
        return true;
      }
    }

    _update() {
      try {
        this._doUpdate();
      } catch (e) {
        // Never let a rendering problem break the dashboard.
        console.error('[electros-card]', e);
      }
    }

    _doUpdate() {
      if (!this._r) this._build();
      const r = this._r;
      const h = this._hass;
      const lang = (this._lang = langOf(h));
      setClass(r.card, this._rootClass() + (this._isDark() ? ' dark' : ' light'));
      if (!h) {
        this._empty(tr(lang, 'loading'), '');
        return;
      }
      if (!this._config.device_id) {
        this._empty(tr(lang, 'empty_pick_title'), tr(lang, 'empty_pick_text'));
        return;
      }
      const map = this._resolve();
      if (!Object.keys(map).length) {
        this._empty(tr(lang, 'empty_nf_title'), tr(lang, 'empty_nf_text'));
        return;
      }
      const m = computeModel(h, map, Date.now());
      this._applyPending(m);
      this._m = m;
      this._checkBusy(m);
      this._render(m, lang, h);
    }

    _rootClass() {
      return '';
    }

    /* Optimistic values for switches/numbers/times until hass confirms them (or 8 s pass). */
    _pend(key, hassVal) {
      const p = this._pending[key];
      if (!p) return hassVal;
      if (Date.now() > p.until || p.value === hassVal) {
        delete this._pending[key];
        return hassVal;
      }
      return p.value;
    }

    _setPending(key, value) {
      this._pending[key] = { value, until: Date.now() + 8000 };
    }

    _applyPending(m) {
      const night = this._pend('night', m.nightOn);
      const wk = this._pend('weekend', m.weekendOn);
      const ts = this._pend('tStart', m.tStart);
      const te = this._pend('tEnd', m.tEnd);
      if (night !== m.nightOn || wk !== m.weekendOn || ts !== m.tStart || te !== m.tEnd) {
        m.nightOn = night;
        m.weekendOn = wk;
        m.tStart = ts;
        m.tEnd = te;
        derive(m);
      }
    }

    _setBusy(kind) {
      const m = this._m;
      this._busy = { kind, from: m ? m.status : null, until: Date.now() + 10000 };
    }

    _checkBusy(m) {
      const b = this._busy;
      if (!b) return null;
      if (Date.now() > b.until || m.status !== b.from) {
        this._busy = null;
        return null;
      }
      return b.kind;
    }

    /* A chip tapped a moment ago wins; otherwise a running boost is mirrored by the chips. */
    _freshTap(kind) {
      return this._tap && this._tap.kind === kind && Date.now() - this._tap.at < 8000;
    }

    _selection(m) {
      const q = this._config.quick_minutes;
      const own = this._sel !== undefined && (this._sel === 0 || q.includes(this._sel));
      if (m.charging && m.boostActive && !(own && this._freshTap('time'))) {
        if (m.mode === 'timed') return q.includes(m.boostMinutes) ? m.boostMinutes : null;
        if (m.mode === 'unlimited') return 0;
        if (m.mode === 'energy') return null;
      }
      if (own) return this._sel;
      if (m.boostSetting !== null && q.includes(m.boostSetting)) return m.boostSetting;
      if (q.includes(30)) return 30;
      return q[0];
    }

    _selectionKwh(m) {
      const q = this._config.quick_kwh;
      const own = this._selKwh !== undefined && q.includes(this._selKwh);
      if (m.charging && m.boostActive && !(own && this._freshTap('energy'))) {
        return m.mode === 'energy' && q.includes(m.targetKwh) ? m.targetKwh : null;
      }
      if (own) return this._selKwh;
      if (m.boostKwhSetting !== null && q.includes(m.boostKwhSetting)) return m.boostKwhSetting;
      if (q.includes(10)) return 10;
      return q[0];
    }

    /* "Час | Енергія": the user's choice, else the running boost, else the configured default. */
    _chargeMode(m) {
      if (!m.hasEnergy) return 'time';
      if (this._cmode) return this._cmode;
      if (m.charging && m.mode === 'energy') return 'energy';
      if (m.charging && (m.mode === 'timed' || m.mode === 'unlimited')) return 'time';
      return this._config.charge_mode || 'time';
    }

    /* Start with whatever the charge panel currently offers (time chip or energy chip). */
    _startSelected(m) {
      if (this._chargeMode(m) === 'energy') {
        const k = this._selectionKwh(m);
        if (k > 0) return this._startEnergy(k);
        return Promise.resolve();
      }
      return this._startCharge(this._selection(m));
    }

    _canStartSelected(m) {
      if (this._chargeMode(m) === 'energy') return this._selectionKwh(m) > 0;
      const sel = this._selection(m);
      return sel > 0 || (sel === 0 && !!this._map['switch.charging']);
    }

    _call(domain, service, data) {
      const h = this._hass;
      if (!h || typeof h.callService !== 'function') return Promise.resolve();
      let p;
      try {
        p = h.callService(domain, service, data);
      } catch (e) {
        this._toast(e);
        return Promise.resolve();
      }
      return Promise.resolve(p).catch((e) => {
        this._toast(e);
        this._busy = null;
        this._pending = {};
        this._update();
      });
    }

    _toast(e) {
      const msg = (e && (e.message || e.code)) || String(e);
      fire(this, 'hass-notification', { message: tr(this._lang, 'error_call', { e: msg }) });
    }

    _moreInfo() {
      const id = this._map['sensor.status'] || Object.values(this._map)[0];
      if (id) fire(this, 'hass-more-info', { entityId: id });
    }

    _deviceId() {
      return this._config.device_id;
    }

    _startCharge(minutes) {
      if (minutes > 0) {
        return this._call(DOMAIN, 'charge_for', { device_id: this._deviceId(), minutes });
      }
      const sw = this._map['switch.charging'];
      if (sw) return this._call('switch', 'turn_on', { entity_id: sw });
      return Promise.resolve();
    }

    _stopCharge() {
      return this._call(DOMAIN, 'stop_charging', { device_id: this._deviceId() });
    }

    _extend(minutes) {
      return this._call(DOMAIN, 'extend_charge', { device_id: this._deviceId(), minutes });
    }

    _startEnergy(kwh) {
      return this._call(DOMAIN, 'charge_energy', { device_id: this._deviceId(), kwh });
    }

    _extendKwh(kwh) {
      return this._call(DOMAIN, 'extend_charge', { device_id: this._deviceId(), kwh });
    }

    /* "4,2 з 10 кВт·год" */
    _ofTarget(m, lang, loc) {
      return tr(lang, 'of_target', { a: fmtNum(m.deliveredKwh, 1, loc), b: m.targetKwh !== null ? fmtK(m.targetKwh, loc) : '—' })
        + ' ' + tr(lang, 'kwh');
    }

    /* "+15 хв" for a timed boost, "+5 кВт·год" for an energy boost. */
    _plus() {
      const m = this._m;
      if (!m || m.offline) return;
      if (m.mode === 'energy') this._extendKwh(EXTEND_KWH);
      else this._extend(EXTEND_MIN);
    }

    _plusLabel(m, lang) {
      const loc = numLocale(this._hass);
      if (m.mode === 'energy') {
        const k = fmtKwh(lang, EXTEND_KWH, loc);
        return { text: '+' + k, aria: tr(lang, 'plus_kwh_aria', { k }) };
      }
      return { text: tr(lang, 'plus15'), aria: tr(lang, 'plus15_aria') };
    }
  }

  /* ------------------------------------------------------------- full card */

  class ElectrosCard extends ElectrosBase {
    static getConfigElement() {
      return document.createElement('electros-card-editor');
    }

    static getStubConfig(hass) {
      const dev = firstDevice(hass);
      const cfg = { quick_minutes: DEFAULT_QUICK.slice(), theme: 'auto' };
      if (dev) cfg.device_id = dev;
      return cfg;
    }

    getCardSize() {
      return 24;
    }

    getGridOptions() {
      return { columns: 12, min_columns: 6, rows: 'auto' };
    }

    _build() {
      this.shadowRoot.innerHTML = '<style>' + CARD_CSS + '</style>' + CARD_HTML(this._uid);
      const r = (this._r = collectRefs(this.shadowRoot));
      r.settings.addEventListener('click', () => this._moreInfo());
      r.mainBtn.addEventListener('click', () => this._onMain());
      r.plus.addEventListener('click', () => this._plus());
      r.segTime.addEventListener('click', () => this._setMode('time'));
      r.segEnergy.addEventListener('click', () => this._setMode('energy'));
      this._bindStats();
      r.nightSw.addEventListener('click', () => this._toggleSwitch('night', 'switch.night_timer'));
      r.wkSw.addEventListener('click', () => this._toggleSwitch('weekend', 'switch.weekend_free'));
      r.nightLine.addEventListener('click', () => {
        this._editOpen = !this._editOpen;
        this._update();
        if (this._editOpen) {
          try {
            r.tStart.focus();
          } catch (e) { /* ignore */ }
        }
      });
      r.nightDone.addEventListener('click', () => {
        this._editOpen = false;
        this._update();
        try {
          r.nightLine.focus();
        } catch (e) { /* ignore */ }
      });
      r.tStart.addEventListener('change', () => this._onTime('tStart', 'time.timer_start', r.tStart.value));
      r.tEnd.addEventListener('change', () => this._onTime('tEnd', 'time.timer_end', r.tEnd.value));
      this._bindAmps();
      this._bindSlide();
    }

    _empty(title, text) {
      const r = this._r;
      show(r.main, false);
      show(r.empty, true);
      setText(r.emptyTitle, title);
      setText(r.emptyText, text);
      show(r.emptyText, !!text);
      setClass(r.glow, 'glow off');
    }

    _onMain() {
      const m = this._m;
      if (!m || m.offline || this._busy) return;
      if (m.charging) {
        this._setBusy('stop');
        this._stopCharge();
      } else {
        if (!this._canStartSelected(m)) return;
        this._setBusy('start');
        this._startSelected(m);
      }
      this._update();
    }

    _setMode(mode) {
      this._cmode = mode;
      this._update();
    }

    _onChip(kind, val) {
      const m = this._m;
      this._tap = { kind, at: Date.now() };
      if (kind === 'energy') {
        this._selKwh = val;
        // While charging an energy chip re-targets the session: N kWh from now.
        if (m && m.charging && !m.offline
          && !(m.mode === 'energy' && m.targetKwh === val && m.deliveredKwh < 0.05)) this._startEnergy(val);
        this._update();
        return;
      }
      const minutes = val;
      this._sel = minutes;
      // While charging a chip re-times the session (as in the approved mock-up).
      if (m && m.charging && !m.offline) {
        if (minutes > 0) {
          if (!(m.mode === 'timed' && m.boostMinutes === minutes && m.remaining > minutes * 60 - 5)) this._startCharge(minutes);
        } else if (m.mode !== 'unlimited') {
          // ∞ while a timed/energy boost runs: the backend turns it into an unlimited bypass
          this._startCharge(0);
        }
      }
      this._update();
    }

    _toggleSwitch(key, contractKey) {
      const m = this._m;
      const id = this._map[contractKey];
      if (!m || !id) return;
      const cur = key === 'night' ? m.nightOn : m.weekendOn;
      this._setPending(key, !cur);
      this._call('switch', 'toggle', { entity_id: id });
      this._update();
    }

    _onTime(key, contractKey, value) {
      const min = parseHM(value);
      const id = this._map[contractKey];
      if (min === null || !id) return;
      this._setPending(key, min);
      // typing "2","2","3","0" fires several change events: send only the settled value
      this._timeT = this._timeT || {};
      clearTimeout(this._timeT[key]);
      this._timeT[key] = setTimeout(() => {
        this._call('time', 'set_value', { entity_id: id, time: fmtHM(min) + ':00' });
      }, 700);
      this._update();
    }

    _bindAmps() {
      const r = this._r;
      const inp = r.slIn;
      const startDrag = () => {
        this._ampsDrag = true;
      };
      inp.addEventListener('pointerdown', startDrag);
      inp.addEventListener('touchstart', startDrag, { passive: true });
      inp.addEventListener('input', () => {
        this._ampsDrag = true;
        this._ampsLocal = Number(inp.value);
        if (this._m) this._paintAmps(this._m, this._ampsLocal);
      });
      inp.addEventListener('change', () => {
        const v = Number(inp.value);
        this._ampsDrag = false;
        this._ampsLocal = undefined;
        this._setPending('amps', v);
        clearTimeout(this._ampsT);
        const id = this._map['number.charging_amps'];
        this._ampsT = setTimeout(() => {
          if (id) this._call('number', 'set_value', { entity_id: id, value: v });
        }, 400);
        this._update();
      });
      const endDrag = () => {
        // "change" normally follows; this only unlocks the value if it never arrives.
        setTimeout(() => {
          if (this._ampsDrag && this._ampsLocal === undefined) this._ampsDrag = false;
        }, 0);
      };
      inp.addEventListener('pointerup', endDrag);
      inp.addEventListener('pointercancel', endDrag);
      inp.addEventListener('blur', () => {
        if (this._ampsLocal !== undefined) inp.dispatchEvent(new Event('change'));
      });
    }

    _bindSlide() {
      const r = this._r;
      const knob = r.slideKnob;
      const track = r.slide;
      let x = 0;
      let maxX = 0;
      let startX = 0;
      let pid = null;
      let dragging = false;
      const paint = (px) => {
        knob.style.transform = 'translateX(' + px + 'px)';
        r.slideFill.style.width = px + 64 + 'px';
        r.slideLabel.style.opacity = maxX > 0 ? String(1 - Math.min(1, px / maxX) * 0.85) : '1';
      };
      this._resetSlide = () => {
        x = 0;
        dragging = false;
        track.classList.remove('drag');
        paint(0);
      };
      knob.addEventListener('pointerdown', (e) => {
        if (knob.disabled || this._busy || (e.button !== undefined && e.button !== 0)) return;
        dragging = true;
        pid = e.pointerId;
        const tr0 = track.getBoundingClientRect();
        maxX = Math.max(0, tr0.width - 64);
        startX = e.clientX - x;
        track.classList.add('drag');
        try {
          knob.setPointerCapture(pid);
        } catch (err) { /* ignore */ }
        e.preventDefault();
      });
      knob.addEventListener('pointermove', (e) => {
        if (!dragging || e.pointerId !== pid) return;
        x = clamp(e.clientX - startX, 0, maxX);
        paint(x);
      });
      const end = (e) => {
        if (!dragging || (e && e.pointerId !== undefined && e.pointerId !== pid)) return;
        dragging = false;
        track.classList.remove('drag');
        if (maxX > 0 && x >= maxX * 0.85) {
          paint(maxX);
          x = 0;
          this._slideDone();
        } else {
          x = 0;
          paint(0);
          if (e && e.type === 'pointerup') {
            knob.classList.remove('nudge');
            void knob.offsetWidth;
            knob.classList.add('nudge');
            setTimeout(() => knob.classList.remove('nudge'), 650);
          }
        }
      };
      knob.addEventListener('pointerup', end);
      knob.addEventListener('pointercancel', end);
      knob.addEventListener('lostpointercapture', end);
      // Keyboard / assistive-tech activation (Enter, Space): such clicks carry detail === 0.
      knob.addEventListener('click', (e) => {
        if (e.detail === 0) {
          const w = track.getBoundingClientRect().width;
          maxX = Math.max(0, w - 64);
          paint(maxX);
          this._slideDone();
        }
      });
    }

    _slideDone() {
      const m = this._m;
      if (!m || m.offline || this._busy || m.charging || !this._canStartSelected(m)) {
        if (this._resetSlide) this._resetSlide();
        return;
      }
      this._setBusy('start');
      this._startSelected(m);
      this._update();
    }

    _paintAmps(m, val) {
      const r = this._r;
      const lang = this._lang;
      const loc = numLocale(this._hass);
      const v = Number.isFinite(val) ? clamp(val, m.ampsMin, m.ampsMax) : m.ampsMin;
      const pct = (v - m.ampsMin) / (m.ampsMax - m.ampsMin);
      setText(r.ampsVal, fmtNum(v, Number.isInteger(v) ? 0 : 1, loc));
      setText(r.ampsKw, '≈ ' + fmtNum((v * (m.voltage || 230)) / 1000, 1, loc) + ' ' + tr(lang, 'kw'));
      css(r.slFill, 'width', 'calc(' + (pct * 100).toFixed(2) + '% + ' + (14 - 28 * pct).toFixed(2) + 'px)');
      css(r.slThumb, 'left', 'calc(' + (pct * 100).toFixed(2) + '% - ' + (28 * pct).toFixed(2) + 'px)');
      const count = Math.min(40, Math.round((m.ampsMax - m.ampsMin) / m.ampsStep) + 1);
      const idx = Math.round((v - m.ampsMin) / m.ampsStep);
      if (r.slTicks.__n !== count) {
        const nodes = [];
        for (let i = 0; i < count; i += 1) nodes.push(document.createElement('span'));
        r.slTicks.replaceChildren(...nodes);
        r.slTicks.__n = count;
        r.slTicks.__idx = -1;
      }
      if (r.slTicks.__idx !== idx) {
        const kids = r.slTicks.children;
        for (let i = 0; i < kids.length; i += 1) kids[i].classList.toggle('on', i <= idx);
        r.slTicks.__idx = idx;
      }
      setAttr(r.slIn, 'aria-valuetext', fmtNum(v, 0, loc) + ' ' + tr(lang, 'a'));
    }

    _render(m, lang, h) {
      const r = this._r;
      const cfg = this._config;
      const loc = numLocale(h);
      const busy = this._busy ? this._busy.kind : null;
      const pill = pillFor(m, lang);
      const dis = m.offline;
      show(r.empty, false);
      show(r.main, true);

      // glow
      const glow = m.charging ? 'on' : m.cat === 'fault' ? 'fault'
        : (m.cat === 'offline' || m.cat === 'unknown') ? 'off'
          : (m.heroTime || m.status === 'charging_blocked') ? 'night' : 'idle';
      setClass(r.glow, 'glow ' + glow);

      // header
      const dev = (h.devices && h.devices[cfg.device_id]) || null;
      const area = dev && dev.area_id && h.areas && h.areas[dev.area_id] ? h.areas[dev.area_id].name : '';
      setText(r.title, (dev && dev.name_by_user) || 'ElectroS');
      setText(r.area, area || '');
      show(r.area, !!area);
      setAttr(r.settings, 'aria-label', tr(lang, 'settings'));
      setAttr(r.settings, 'title', tr(lang, 'settings'));

      // pill
      setClass(r.pill, 'pill ' + pill.kind);
      setText(r.pillText, pill.text);
      show(r.pillDot, !pill.icon);
      setClass(r.pillDot, pill.pulse ? 'dot pulse' : 'dot');
      show(r.pillIcon, !!pill.icon);
      if (pill.icon) setIcon(r.pillIcon, pill.icon, 16, 2);

      // ring
      let ringKind;
      let tmpl = 'big';
      let icon;
      let iconCls;
      let val;
      let unit = '';
      let sub = '';
      const kwStr = fmtNum(m.kw, 1, loc);
      if (m.charging) {
        ringKind = 'prog';
        icon = 'bolt';
        iconCls = 'green';
        val = kwStr;
        unit = tr(lang, 'kw');
        const a = m.measured !== null ? m.measured : m.amps;
        const parts = [];
        if (a !== null && a !== undefined) parts.push(fmtNum(a, Number.isInteger(a) ? 0 : 1, loc) + ' ' + tr(lang, 'a'));
        if (m.voltage !== null) parts.push(fmtNum(m.voltage, 0, loc) + ' ' + tr(lang, 'v'));
        sub = parts.join(' · ');
      } else if (m.heroTime) {
        ringKind = 'night';
        tmpl = 'time';
        icon = 'plug';
        iconCls = 'purple';
        val = fmtHM(m.tStart);
        sub = tr(lang, 'start_in', { d: fmtDur(lang, m.minsToStart) });
      } else if (m.status === 'charging_blocked') {
        ringKind = 'night';
        tmpl = 'msg';
        icon = 'lock';
        iconCls = 'purple';
        val = tr(lang, 'blocked_big');
        sub = tr(lang, 'blocked_sub');
      } else if (m.status === 'vehicle_connected_idle') {
        ringKind = 'empty';
        icon = 'bolt';
        iconCls = 'muted';
        val = fmtNum(0, 1, loc);
        unit = tr(lang, 'kw');
        sub = tr(lang, 'ready');
      } else if (m.status === 'waiting_for_vehicle') {
        ringKind = 'grey';
        tmpl = 'msg';
        icon = 'plug';
        iconCls = 'muted';
        val = tr(lang, 'plug_in');
        sub = m.timerEffective
          ? (m.inWin ? tr(lang, 'night_until', { t: fmtHM(m.tEnd) }) : tr(lang, 'night_from', { t: fmtHM(m.tStart) }))
          : tr(lang, 'any_time');
      } else if (m.cat === 'fault') {
        ringKind = 'fault';
        tmpl = 'msg';
        icon = 'warn';
        iconCls = 'red';
        val = tr(lang, 'stopped');
        sub = tr(lang, 'fh_' + m.status);
        if (m.status === 'mains_voltage_low' && m.voltage !== null) sub += ' · ' + fmtNum(m.voltage, 0, loc) + ' ' + tr(lang, 'v');
      } else if (m.offline) {
        ringKind = 'grey';
        tmpl = 'msg';
        icon = 'offline';
        iconCls = 'muted';
        val = tr(lang, 'no_link');
        sub = Number.isFinite(m.statusSince) ? tr(lang, 'offline_since', { t: clockOf(m.statusSince, m.tz) }) : '';
      } else {
        ringKind = 'grey';
        tmpl = 'msg';
        icon = 'question';
        iconCls = 'muted';
        val = '—';
      }
      setClass(r.ring, 'ring t-' + tmpl + (ringKind === 'fault' ? ' fault' : ringKind === 'night' ? ' night' : ''));
      show(r.ringDash, ringKind === 'night' || ringKind === 'grey');
      const showProg = ringKind === 'prog' && m.frac > 0.002;
      show(r.ringProg, showProg);
      show(r.ringDot, showProg);
      if (showProg) {
        setAttr(r.ringProg, 'stroke-dashoffset', (RING_C * (1 - m.frac)).toFixed(2));
        const ang = ((-90 + 360 * m.frac) * Math.PI) / 180;
        setAttr(r.ringDot, 'cx', (136 + RING_R * Math.cos(ang)).toFixed(1));
        setAttr(r.ringDot, 'cy', (136 + RING_R * Math.sin(ang)).toFixed(1));
      }
      setClass(r.ringIcon, 'ic ' + iconCls);
      setIcon(r.ringIcon, icon, tmpl === 'big' ? 22 : 30, tmpl === 'big' ? 2 : 1.8);
      setText(r.ringValue, val);
      setText(r.ringUnit, unit);
      show(r.ringUnit, !!unit);
      setText(r.ringSub, sub);
      setAttr(r.ring, 'aria-label', [val, unit, sub].filter(Boolean).join(' '));

      // countdown (charging only)
      show(r.count, m.charging);
      if (m.charging) {
        let big;
        let small;
        let cIcon = 'timer';
        let cCls = 'blue';
        let plus = true;
        let bigUnit = '';
        if (m.mode === 'energy') {
          // "4,2 з 10 кВт·год" + "≈ о 22:05" (estimated end from sensor.boost_end; unknown while power is 0)
          big = tr(lang, 'of_target', { a: fmtNum(m.deliveredKwh, 1, loc), b: m.targetKwh !== null ? fmtK(m.targetKwh, loc) : '—' });
          bigUnit = tr(lang, 'kwh');
          small = m.etaMs !== null ? tr(lang, 'eta_at', { t: clockOf(m.etaMs, m.tz) }) : tr(lang, 'eta_unknown');
          cIcon = 'battery';
        } else if (m.mode === 'timed') {
          big = fmtCountdown(m.remaining);
          small = tr(lang, 'until_stop_at', { t: clockOf(m.boostEnd, m.tz) });
        } else if (m.mode === 'unlimited') {
          big = tr(lang, 'no_limit');
          small = tr(lang, 'until_full');
        } else if (m.mode === 'schedule') {
          big = tr(lang, 'until_t', { t: fmtHM(m.tEnd) });
          small = tr(lang, 'schedule_left', { d: fmtDur(lang, m.minsToEnd) });
          cIcon = 'moon';
          cCls = 'purple';
          plus = false;
        } else {
          big = tr(lang, 'no_limit');
          small = tr(lang, 'until_full');
          cIcon = 'bolt';
          cCls = 'green';
          plus = false;
        }
        setClass(r.countIcon, 'ic ' + cCls);
        setIcon(r.countIcon, cIcon, 26, 1.8);
        setText(r.countBig, big);
        setText(r.countUnit, bigUnit);
        show(r.countUnit, !!bigUnit);
        setText(r.countSub, small);
        show(r.plus, plus);
        const pl = this._plusLabel(m, lang);
        setText(r.plus, pl.text);
        setAttr(r.plus, 'aria-label', pl.aria);
        setDisabled(r.plus, dis);
      }

      // last boost problem from the backend (e.g. the energy boost hit its safety deadline)
      show(r.boostErr, !!m.lastError);
      if (m.lastError) setText(r.boostErrText, lastErrorText(lang, m.lastError));

      // "need it now?" hint
      const hint = m.bypass && m.status !== 'waiting_for_vehicle';
      show(r.hint, hint);
      if (hint) {
        setText(r.hintTitle, tr(lang, 'need_now'));
        setText(r.hintText, tr(lang, 'need_now_text'));
      }

      // stats (charging only)
      show(r.stats, m.charging);
      if (m.charging) {
        setText(r.s1l, tr(lang, 'session'));
        setText(r.s1v, fmtNum(m.session !== null ? m.session : 0, 1, loc));
        setText(r.s1u, ' ' + tr(lang, 'kwh'));
        setText(r.s2l, tr(lang, 'duration'));
        setText(r.s2v, Number.isFinite(m.startedAt) ? fmtElapsed((m.now - m.startedAt) / 1000) : '—');
        setText(r.s2u, '');
        if (m.sessionCost !== null) {
          // sensor.session_energy.cost — the backend prices each kWh by the tariff zone it was charged in
          setText(r.s3l, tr(lang, 'cost'));
          setText(r.s3v, fmtMoney(m.sessionCost, h, 2));
          setText(r.s3u, '');
        } else {
          setText(r.s3l, tr(lang, 'voltage'));
          setText(r.s3v, fmtNum(m.voltage, 0, loc));
          setText(r.s3u, m.voltage !== null ? ' ' + tr(lang, 'v') : '');
        }
      }

      this._renderCharge(m, lang, busy, dis);
      this._renderAmps(m, lang, dis);
      this._renderNight(m, lang, dis);
      this._renderEnergy(m, lang, h);
    }

    _renderCharge(m, lang, busy, dis) {
      const r = this._r;
      const h = this._hass;
      const loc = numLocale(h);
      const canStart = !!(this._map['sensor.status'] || this._map['switch.charging']);
      show(r.chargePanel, canStart);
      if (!canStart) return;
      const mode = this._chargeMode(m);
      const energy = mode === 'energy';
      const q = energy ? this._config.quick_kwh : this._config.quick_minutes;
      const sel = energy ? this._selectionKwh(m) : this._selection(m);
      setText(r.chargeTitle, tr(lang, 'charge_for'));
      let sub;
      if (m.charging) {
        sub = tr(lang, 'then_off');
      } else if (energy) {
        // rough preview at the configured current: "≈ 1 год 25 хв · ≈ 43 ₴"
        const parts = [];
        const kw = ((m.amps || 0) * (m.voltage || 230)) / 1000;
        if (sel > 0 && kw > 0) parts.push(tr(lang, 'approx', { v: fmtDur(lang, (sel / kw) * 60) }));
        if (sel > 0 && m.priceNow !== null) parts.push(tr(lang, 'approx', { v: fmtMoney(sel * m.priceNow, h, 0) }));
        sub = parts.join(' · ');
      } else {
        sub = sel > 0 ? tr(lang, 'until_t', { t: clockOf(m.now + sel * 60000, m.tz) }) : tr(lang, 'until_manual');
      }
      setText(r.chargeSub, sub);

      // "Час | Енергія" — only when the backend offers charge_energy
      show(r.modeSeg, m.hasEnergy);
      if (m.hasEnergy) {
        setAttr(r.modeSeg, 'aria-label', tr(lang, 'mode_aria'));
        setText(r.segTimeT, tr(lang, 'seg_time'));
        setText(r.segEnergyT, tr(lang, 'seg_energy'));
        setAttr(r.segTime, 'aria-pressed', energy ? 'false' : 'true');
        setAttr(r.segEnergy, 'aria-pressed', energy ? 'true' : 'false');
      }

      // chips: rebuilt only when the list, mode or language changes
      const sig = mode + '|' + q.join(',') + '|' + lang + '|' + loc;
      if (r.chips.__sig !== sig) {
        const vals = energy ? q.slice() : q.concat([0]);
        const units = q.length <= 4;
        const nodes = vals.map((v) => {
          const b = document.createElement('button');
          b.type = 'button';
          b.className = 'chip';
          if (energy) {
            const n = document.createElement('span');
            n.textContent = fmtK(v, loc);
            b.appendChild(n);
            if (units) {
              const u = document.createElement('span');
              u.className = 'cu';
              u.textContent = tr(lang, 'kwh');
              b.appendChild(u);
            }
            b.setAttribute('aria-label', fmtKwh(lang, v, loc));
          } else {
            b.textContent = chipLabel(lang, v);
            b.setAttribute('aria-label', v ? fmtDur(lang, v) : tr(lang, 'no_limit'));
          }
          b.__val = v;
          b.addEventListener('click', () => this._onChip(mode, v));
          return b;
        });
        r.chips.replaceChildren(...nodes);
        r.chips.__sig = sig;
      }
      setAttr(r.chips, 'aria-label', tr(lang, 'charge_for'));
      const kids = r.chips.children;
      for (let i = 0; i < kids.length; i += 1) {
        setAttr(kids[i], 'aria-pressed', kids[i].__val === sel ? 'true' : 'false');
        setDisabled(kids[i], dis);
      }

      const what = energy ? (sel > 0 ? fmtKwh(lang, sel, loc) : null) : (sel > 0 ? fmtDur(lang, sel) : null);
      const useSlide = !m.charging && m.bypass;
      show(r.mainBtn, !useSlide);
      show(r.slide, useSlide);
      if (!useSlide) {
        if (m.charging) {
          setClass(r.mainBtn, 'main stop');
          setIcon(r.mainIcon, 'stop', 20, 2.2);
          setText(r.mainLabel, busy === 'stop' ? tr(lang, 'stopping') : tr(lang, 'stop'));
        } else if (m.cat === 'fault') {
          // after a protection trip: a quiet retry, not the green "charge" call to action
          setClass(r.mainBtn, 'main neutral');
          setIcon(r.mainIcon, 'restore', 20, 2.2);
          setText(r.mainLabel, busy === 'start' ? tr(lang, 'starting') : tr(lang, 'retry'));
        } else {
          setClass(r.mainBtn, 'main');
          setIcon(r.mainIcon, 'play', 20, 2.2);
          setText(r.mainLabel, busy === 'start' ? tr(lang, 'starting')
            : what ? tr(lang, energy ? 'charge_kwh' : 'charge_n', { d: what, k: what }) : tr(lang, 'start'));
        }
        setDisabled(r.mainBtn, dis || !!busy || (!m.charging && !this._canStartSelected(m)));
      } else {
        setText(r.slideLabel, busy === 'start' ? tr(lang, 'starting') : tr(lang, 'slide'));
        setAttr(r.slideKnob, 'aria-label', what
          ? tr(lang, energy ? 'slide_aria_kwh' : 'slide_aria', { d: what, k: what }) : tr(lang, 'slide_aria_inf'));
        setDisabled(r.slideKnob, dis || !this._canStartSelected(m));
        setClass(r.slide, 'slide' + (dis ? ' dis' : '') + (r.slide.classList.contains('drag') ? ' drag' : ''));
        if (!busy && this._resetSlide && !r.slide.classList.contains('drag') && r.slideKnob.style.transform
          && r.slideKnob.style.transform !== 'translateX(0px)') {
          this._resetSlide();
        }
      }
    }

    _renderAmps(m, lang, dis) {
      const r = this._r;
      show(r.ampsPanel, m.hasAmps);
      if (!m.hasAmps) return;
      setText(r.ampsTitle, tr(lang, 'current'));
      setText(r.ampsUnit, ' ' + tr(lang, 'a'));
      const loc = numLocale(this._hass);
      setAttr(r.slIn, 'min', m.ampsMin);
      setAttr(r.slIn, 'max', m.ampsMax);
      setAttr(r.slIn, 'step', m.ampsStep);
      setAttr(r.slIn, 'aria-label', tr(lang, 'current_aria'));
      setDisabled(r.slIn, dis || !m.ampsAvail);
      setClass(r.slider, 'slider' + (dis || !m.ampsAvail ? ' dis' : ''));
      setText(r.slMin, fmtNum(m.ampsMin, 0, loc) + ' ' + tr(lang, 'a'));
      setText(r.slMax, fmtNum(m.ampsMax, 0, loc) + ' ' + tr(lang, 'a'));
      const mid = m.ampsMin < 16 && m.ampsMax > 16 ? 16 : Math.round((m.ampsMin + m.ampsMax) / 2);
      setText(r.slMid, fmtNum(mid, 0, loc) + ' ' + tr(lang, 'a'));
      css(r.slMid, 'left', 'calc(' + (((mid - m.ampsMin) / (m.ampsMax - m.ampsMin)) * 100).toFixed(2) + '% + '
        + (14 - 28 * ((mid - m.ampsMin) / (m.ampsMax - m.ampsMin))).toFixed(2) + 'px)');
      let v;
      if (this._ampsDrag && this._ampsLocal !== undefined) {
        v = this._ampsLocal; // user is dragging: never fight the thumb
      } else {
        v = this._pend('amps', m.amps);
        if (!this._ampsDrag && v !== null && v !== undefined && String(v) !== this._r.slIn.value) this._r.slIn.value = String(v);
      }
      this._paintAmps(m, v === null || v === undefined ? m.ampsMin : v);
    }

    _renderNight(m, lang, dis) {
      const r = this._r;
      const has = m.hasNight || m.hasStart || m.hasEnd;
      show(r.nightPanel, has);
      if (!has) return;
      setText(r.nightTitle, tr(lang, 'night'));
      const win = fmtHM(m.tStart) + ' → ' + fmtHM(m.tEnd);
      let line;
      if (!m.nightOn) line = tr(lang, 'night_off');
      else if (m.weekendOn && m.weekendToday) line = win + ' · ' + tr(lang, 'weekend_today');
      else if (m.heroTime) line = win + ' · ' + tr(lang, m.weekendOn ? 'weekdays' : 'daily');
      else if (m.inWin) line = win + ' · ' + tr(lang, 'active_left', { d: fmtDur(lang, m.minsToEnd) });
      else line = win + ' · ' + tr(lang, 'start_in', { d: fmtDur(lang, m.minsToStart) });
      setText(r.nightLine, line);
      const canEdit = (m.hasStart || m.hasEnd) && !dis;
      setDisabled(r.nightLine, !canEdit);
      setAttr(r.nightLine, 'aria-expanded', canEdit ? (this._editOpen ? 'true' : 'false') : null);
      setAttr(r.nightLine, 'aria-label', canEdit ? line + '. ' + tr(lang, 'edit_hours') : null);
      setAttr(r.nightLine, 'title', canEdit ? tr(lang, 'edit_hours') : null);

      show(r.nightSw, m.hasNight);
      setAttr(r.nightSw, 'aria-checked', m.nightOn ? 'true' : 'false');
      setAttr(r.nightSw, 'aria-label', tr(lang, 'night'));
      setDisabled(r.nightSw, dis);

      const open = !!this._editOpen && canEdit;
      show(r.nightEdit, open);
      if (open) {
        setText(r.lblStart, tr(lang, 'start_lbl'));
        setText(r.lblEnd, tr(lang, 'end_lbl'));
        setText(r.nightDone, tr(lang, 'done'));
        const active = this.shadowRoot.activeElement;
        if (active !== r.tStart && r.tStart.value !== fmtHM(m.tStart)) r.tStart.value = fmtHM(m.tStart);
        if (active !== r.tEnd && r.tEnd.value !== fmtHM(m.tEnd)) r.tEnd.value = fmtHM(m.tEnd);
        setDisabled(r.tStart, !m.hasStart);
        setDisabled(r.tEnd, !m.hasEnd);
      }

      // 24 h timeline; the window may wrap midnight
      setClass(r.tl, 'tl' + (m.nightOn ? ' on' : ''));
      const s = m.tStart;
      const e = m.tEnd;
      const P = (min) => ((min / 1440) * 100).toFixed(2) + '%';
      if (s === e) {
        css(r.segA, 'left', '0%');
        css(r.segA, 'width', '100%');
        show(r.segB, false);
      } else if (s < e) {
        css(r.segA, 'left', P(s));
        css(r.segA, 'width', P(e - s));
        show(r.segB, false);
      } else {
        css(r.segA, 'left', P(s));
        css(r.segA, 'width', P(1440 - s));
        show(r.segB, e > 0);
        css(r.segB, 'left', '0%');
        css(r.segB, 'width', P(e));
      }
      css(r.tlNow, 'left', P(clamp(m.nowMin, 0.5, 1439.5)));

      // orange "outside the schedule" note
      const bypassNote = m.charging && m.nightOn && (m.boostActive || !m.inWin);
      show(r.bypass, bypassNote);
      if (bypassNote) {
        let note;
        if (m.mode === 'timed') note = tr(lang, 'bypass_at', { t: clockOf(m.boostEnd, m.tz) });
        else if (m.mode === 'energy' && m.targetKwh !== null) note = tr(lang, 'bypass_energy', { k: fmtKwh(lang, m.targetKwh, numLocale(this._hass)) });
        else note = tr(lang, 'bypass_stop');
        setText(r.bypassText, note);
      }

      show(r.wkRow, m.hasWeekend);
      if (m.hasWeekend) {
        setText(r.wkLabel, tr(lang, 'weekend'));
        setAttr(r.wkSw, 'aria-checked', m.weekendOn ? 'true' : 'false');
        setAttr(r.wkSw, 'aria-label', tr(lang, 'weekend'));
        setDisabled(r.wkSw, dis);
      }
    }

    _renderEnergy(m, lang, h) {
      const r = this._r;
      const ids = this._statIds();
      const useStats = !!ids && !!h && typeof h.callWS === 'function';
      const has = m.hasSession || m.hasMonth || useStats;
      show(r.energyPanel, has);
      if (!has) return;
      const loc = numLocale(h);
      setText(r.energyTitle, tr(lang, 'energy'));
      if (m.hasMonth && m.month !== null) {
        const mo = m.curMonth >= 1 && m.curMonth <= 12 ? m.curMonth : m.nowMonth;
        setText(r.energySub, monthName(lang, mo) + ' · ' + fmtNum(m.month, 1, loc) + ' ' + tr(lang, 'kwh'));
      } else {
        setText(r.energySub, '');
      }
      // HA long-term statistics of the tariff sensors; the session bars stay as the fallback for older backends
      show(r.statBox, useStats);
      show(r.sessBox, !useStats);
      if (useStats) {
        this._renderStats(m, lang, h, ids);
        return;
      }

      const list = m.history.slice(0, 5).reverse().map((x) => ({
        label: String(x.date === undefined || x.date === null ? '' : x.date),
        kwh: Math.max(0, finite(x.kwh, 0)),
      }));
      if (m.charging) list.push({ label: tr(lang, 'now_bar'), kwh: Math.max(0, m.session || 0) });
      show(r.barsEmpty, !list.length);
      show(r.bars, !!list.length);
      show(r.barsLbl, !!list.length);
      if (!list.length) {
        setText(r.barsEmpty, tr(lang, 'no_sessions'));
        return;
      }
      const max = Math.max(...list.map((b) => b.kwh), 0.1);
      const sig = JSON.stringify(list) + '|' + lang + '|' + loc;
      if (r.bars.__sig !== sig) {
        const unit = tr(lang, 'kwh');
        const bars = list.map((b, i) => {
          const col = document.createElement('div');
          col.className = 'bar' + (i === list.length - 1 ? ' hi' : '');
          const v = document.createElement('span');
          v.className = 'bv';
          v.textContent = fmtNum(b.kwh, 1, loc);
          const bb = document.createElement('div');
          bb.className = 'bb';
          bb.style.setProperty('height', Math.max(6, Math.round((b.kwh / max) * 92)) + 'px');
          col.appendChild(v);
          col.appendChild(bb);
          return col;
        });
        const lbls = list.map((b) => {
          const s = document.createElement('span');
          s.textContent = b.label;
          return s;
        });
        r.bars.replaceChildren(...bars);
        r.barsLbl.replaceChildren(...lbls);
        setAttr(r.bars, 'aria-label', tr(lang, 'energy_aria') + ': '
          + list.map((b) => b.label + ' ' + fmtNum(b.kwh, 1, loc) + ' ' + unit).join(', '));
        r.bars.__sig = sig;
      }
    }

    /* ------------------------------------------------ statistics (День · Тиждень · Місяць · Рік) */

    _statIds() {
      const d = this._map['sensor.energy_day_tariff'];
      const n = this._map['sensor.energy_night_tariff'];
      if (!d || !n) return null;
      const c = this._map['sensor.energy_cost'];
      return c ? [d, n, c] : [d, n];
    }

    _bindStats() {
      const r = this._r;
      this._statSel = null;
      this._statBy = null;
      r.perPrev.addEventListener('click', () => this._statNav(-1));
      r.perNext.addEventListener('click', () => this._statNav(1));
      const pick = (e, how) => {
        const n = this._statN || 0;
        if (!n) return;
        const rect = r.plot.getBoundingClientRect();
        if (!rect || !rect.width) return;
        const i = clamp(Math.floor(((e.clientX - rect.left) / rect.width) * n), 0, n - 1);
        if (how === 'click' && this._statBy === 'click' && this._statSel === i) this._setStatSel(null, null);
        else this._setStatSel(i, how);
      };
      // mouse/pen hover reads a bar; touch taps toggle it (touch moves stay page scrolls)
      r.plot.addEventListener('pointermove', (e) => {
        if (e.pointerType === 'mouse' || e.pointerType === 'pen') pick(e, 'hover');
      });
      r.plot.addEventListener('pointerleave', () => {
        if (this._statBy === 'hover') this._setStatSel(null, null);
      });
      r.plot.addEventListener('click', (e) => pick(e, 'click'));
      r.chart.addEventListener('keydown', (e) => {
        const n = this._statN || 0;
        if (!n) return;
        const last = this._statLast >= 0 ? this._statLast : n - 1;
        const cur = this._statSel;
        let i;
        if (e.key === 'ArrowRight') i = cur === null ? 0 : Math.min(n - 1, cur + 1);
        else if (e.key === 'ArrowLeft') i = cur === null ? last : Math.max(0, cur - 1);
        else if (e.key === 'Home') i = 0;
        else if (e.key === 'End') i = last;
        else if (e.key === 'Escape') i = null;
        else return;
        e.preventDefault();
        this._setStatSel(i, i === null ? null : 'key');
      });
    }

    _setStatSel(i, how) {
      if (this._statSel === i && this._statBy === how) return;
      this._statSel = i;
      this._statBy = how;
      this._update();
    }

    _statNav(d) {
      const off = (this._statOffset || 0) + d;
      if (off > 0) return;
      this._statOffset = off;
      this._statSel = null;
      this._statBy = null;
      this._update();
    }

    _setPeriod(p) {
      if (this._statPeriod === p && !this._statOffset) return;
      this._statPeriod = p;
      this._statOffset = 0;
      this._statSel = null;
      this._statBy = null;
      this._update();
    }

    /* One recorder/statistics_during_period call per view; results are cached per period. */
    _loadStats(rg, ids, key) {
      const h = this._hass;
      const cache = this._statCache || (this._statCache = new Map());
      let e = cache.get(key);
      if (!e) {
        e = { buckets: null, status: 'loading', at: 0, okAt: 0, rev: 0, inflight: false };
        cache.set(key, e);
        while (cache.size > 48) cache.delete(cache.keys().next().value);
      }
      e.inflight = true;
      e.at = Date.now();
      const msg = {
        type: 'recorder/statistics_during_period',
        start_time: new Date(rg.start).toISOString(),
        end_time: new Date(rg.end).toISOString(),
        statistic_ids: ids.slice(),
        period: rg.stat,
        types: ['change'],
        units: { energy: 'kWh' },
      };
      let p;
      try {
        p = h.callWS(msg);
      } catch (err) {
        p = Promise.reject(err);
      }
      Promise.resolve(p).then((res) => {
        e.buckets = mergeStats(rg, ids, res);
        e.status = 'ok';
        e.okAt = Date.now();
      }, (err) => {
        // a failed refresh keeps the last good data; a failed first load retries after a minute
        e.status = e.buckets ? 'ok' : 'error';
        // eslint-disable-next-line no-console
        console.warn('[electros-card] statistics:', (err && (err.message || err.code)) || err);
      }).then(() => {
        e.inflight = false;
        e.at = Date.now();
        e.rev += 1;
        this._update();
      });
    }

    _periodTitle(rg, lang, tz) {
      const b0 = rg.buckets[0].start;
      if (rg.period === 'day') {
        if (rg.offset === 0) return tr(lang, 'today');
        if (rg.offset === -1) return tr(lang, 'yesterday');
        const opts = zoneParts(b0, tz).y === rg.nowYear
          ? { weekday: 'short', day: 'numeric', month: 'long' } : { day: 'numeric', month: 'long', year: 'numeric' };
        return dtf(lang, tz, opts).format(b0);
      }
      if (rg.period === 'week') {
        const last = rg.buckets[rg.buckets.length - 1].start;
        const f = dtf(lang, tz, { day: 'numeric', month: 'short' });
        const y = zoneParts(last, tz).y;
        return f.format(b0) + ' – ' + f.format(last) + (y !== rg.nowYear ? ' ' + y : '');
      }
      const p = zoneParts(b0, tz);
      if (rg.period === 'month') {
        const name = monthName(lang, p.mo);
        return name.charAt(0).toUpperCase() + name.slice(1) + ' ' + p.y;
      }
      return String(p.y);
    }

    _renderStats(m, lang, h, ids) {
      const r = this._r;
      const loc = numLocale(h);
      const tz = m.tz;
      if (!this._statPeriod) this._statPeriod = this._config.stats_period;
      const period = this._statPeriod;
      const offset = this._statOffset || 0;
      const np = zoneParts(m.now, tz);
      const rkey = period + '|' + offset + '|' + np.y + '-' + np.mo + '-' + np.d + '|' + (tz || '');
      if (!this._srange || this._srange.rkey !== rkey) {
        this._srange = statRange(period, offset, m.now, tz);
        this._srange.rkey = rkey;
      }
      const rg = this._srange;
      const key = rg.stat + '|' + rg.start + '|' + rg.end + '|' + ids.join(',');
      const cache = this._statCache || (this._statCache = new Map());
      let e = cache.get(key);
      // the current period is re-read at most once a minute (and only while the card is on
      // screen: HA keeps feeding hass to cached views). A past period is final once it was
      // read well after it ended: HA compiles an hour's statistics only after that hour, so
      // a view cached while it was current (e.g. today at 23:59) still misses its last hour.
      const final = !!e && e.okAt > rg.end + STAT_FINAL_AFTER;
      const want = !e || (!e.inflight && Date.now() - e.at > STAT_TTL && (offset === 0 || !final));
      if (want && this.isConnected !== false) {
        this._loadStats(rg, ids, key);
        e = cache.get(key);
      }
      if (!e) e = { buckets: null, status: 'loading', rev: 0 };

      if (r.perSeg.__sig !== lang) {
        const nodes = PERIODS.map((p) => {
          const b = document.createElement('button');
          b.type = 'button';
          b.textContent = tr(lang, 'p_' + p);
          b.__p = p;
          b.addEventListener('click', () => this._setPeriod(p));
          return b;
        });
        r.perSeg.replaceChildren(...nodes);
        r.perSeg.__sig = lang;
      }
      setAttr(r.perSeg, 'aria-label', tr(lang, 'period_aria'));
      const segs = r.perSeg.children;
      for (let i = 0; i < segs.length; i += 1) setAttr(segs[i], 'aria-pressed', segs[i].__p === period ? 'true' : 'false');
      if (rg.__tl !== lang) {
        rg.title = this._periodTitle(rg, lang, tz);
        rg.__tl = lang;
      }
      setText(r.perTitle, rg.title);
      setAttr(r.perPrev, 'aria-label', tr(lang, 'prev_aria'));
      setAttr(r.perPrev, 'title', tr(lang, 'prev_aria'));
      setAttr(r.perNext, 'aria-label', tr(lang, 'next_aria'));
      setAttr(r.perNext, 'title', tr(lang, 'next_aria'));
      setDisabled(r.perPrev, offset <= -240);
      setDisabled(r.perNext, offset >= 0);

      if (!e.buckets) {
        // keep the previous frame (dimmed) while another period loads; first load / errors get the box
        const keep = e.status === 'loading' && this._statShown;
        setClass(r.chart, 'chart' + (keep ? ' stale' : ''));
        if (!keep) {
          show(r.chart, false);
          show(r.legend, false);
          show(r.tot, false);
          show(r.save, false);
          show(r.statEmpty, true);
          setText(r.statEmptyT, e.status === 'error' ? tr(lang, 'stat_error') : tr(lang, 'loading'));
          show(r.statEmptyS, false);
          this._statN = 0;
          r.plot.__sig = null;
        }
        return;
      }

      const now = m.now;
      const cols = rg.buckets.map((b, i) => {
        const s = e.buckets[i];
        const future = b.start > now;
        let fb = 0;
        // Рік: what HA has no statistics for falls back to the charger's own counter (not split by zone):
        // whole months before the install, and the part of the install month before HA started counting.
        // The device keeps Jan..Dec slots: up to the current month they are this year, after it last year.
        if (rg.period === 'year' && !future && m.months && m.curMonth >= 1 && m.curMonth <= 12) {
          const mine = (b.year === rg.nowYear && b.month <= m.curMonth) || (b.year === rg.nowYear - 1 && b.month > m.curMonth);
          const counted = s.has ? finite(s.day, 0) + finite(s.night, 0) : 0;
          const rest = mine ? finite(m.months[b.month - 1], 0) - counted : 0;
          if (rest >= 0.05) fb = rest; // ignore rounding slivers
        }
        return { day: s.day, night: s.night, cost: s.hasCost ? s.cost : null, fb, has: s.has, future, cur: b.start <= now && now < b.end };
      });
      const empty = !cols.some((c) => c.has || c.fb > 0);
      setClass(r.chart, 'chart');
      show(r.chart, !empty);
      show(r.legend, !empty);
      show(r.tot, !empty);
      show(r.statEmpty, empty);
      if (empty) {
        setText(r.statEmptyT, tr(lang, 'stat_empty'));
        setText(r.statEmptyS, tr(lang, 'stat_empty_sub'));
        show(r.statEmptyS, true);
        show(r.save, false);
        this._statN = 0;
        this._statShown = false;
        r.plot.__sig = null;
        return;
      }
      const curIdx = cols.findIndex((c) => c.cur);
      const sel = Number.isInteger(this._statSel) && this._statSel < cols.length ? this._statSel : null;
      const sig = [key, e.rev, lang, loc, h.config && h.config.currency, curIdx, sel, m.priceDay, m.priceNight,
        rg.period === 'year' ? JSON.stringify(m.months) + '/' + m.curMonth : ''].join('|');
      this._statShown = true;
      if (r.plot.__sig === sig) return;
      r.plot.__sig = sig;
      this._statN = cols.length;
      let last = -1;
      cols.forEach((c, i) => {
        if (!c.future) last = i;
      });
      this._statLast = last;
      this._drawStats(rg, cols, sel, lang, loc, h, m);
    }

    _drawStats(rg, cols, sel, lang, loc, h, m) {
      const r = this._r;
      const tz = m.tz;
      const H = 120;
      const unit = tr(lang, 'kwh');
      const el = (tag, cls, text) => {
        const n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text !== undefined) n.textContent = text;
        return n;
      };

      // y scale: hairline gridlines at 0, half and a clean maximum; labels in the right gutter
      const top = niceCeil(Math.max(0, ...cols.map((c) => c.day + c.night + c.fb)));
      const grid = [];
      for (const v of [0, top / 2, top]) {
        const y = Math.min(H - 1, (v / top) * H);
        const g = el('div', 'gl');
        g.style.setProperty('bottom', y.toFixed(1) + 'px');
        grid.push(g);
        if (v > 0) {
          const t = el('span', 'yl', fmtNum(v, Number.isInteger(v) ? 0 : 1, loc));
          t.style.setProperty('bottom', y.toFixed(1) + 'px');
          grid.push(t);
        }
      }
      r.sgrid.replaceChildren(...grid);

      // stacked columns: night (cheap) at the base, day (expensive) on top, 2px surface gap between
      const colEls = cols.map((c, i) => {
        const col = el('div', 'col' + (i === sel ? ' on' : ''));
        const parts = [];
        if (c.night > 0) parts.push(['n', c.night]);
        if (c.day > 0) parts.push(['d', c.day]);
        if (c.fb > 0) parts.push(['f', c.fb]);
        if (parts.length) {
          const total = c.day + c.night + c.fb;
          const avail = Math.max(2, (total / top) * H - 2 * (parts.length - 1));
          const stk = el('div', 'stk');
          for (const [k, v] of parts) {
            const sg = el('div', 'sg ' + k);
            sg.style.setProperty('height', Math.max(2, (v / total) * avail).toFixed(1) + 'px');
            stk.appendChild(sg);
          }
          col.appendChild(stk);
        }
        return col;
      });
      r.plot.replaceChildren(...colEls);
      setClass(r.plot, 'plot' + (sel !== null ? ' hs' : ''));

      // x labels (sparse for hours/days so they never collide)
      const wdF = rg.period === 'week' ? dtf(lang, tz, { weekday: 'short' }) : null;
      const moF = rg.period === 'year' ? dtf(lang, tz, { month: 'short' }) : null;
      const xl = rg.buckets.map((b, i) => {
        let t = '';
        if (rg.period === 'day') {
          const hh = zoneParts(b.start, tz).h;
          if (hh % 6 === 0) t = pad(hh);
        } else if (rg.period === 'week') {
          t = wdF.format(b.start);
        } else if (rg.period === 'month') {
          const d = zoneParts(b.start, tz).d;
          if (d === 1 || d % 5 === 0) t = String(d);
        } else {
          // 12 slots are narrow: at most three letters ("січ", "лют", "Jan")
          t = moF.format(b.start).replace(/\.$/, '').slice(0, 3);
        }
        return el('span', cols[i].cur ? 'cur' : '', t);
      });
      r.xl.replaceChildren(...xl);

      // totals
      let dayK = 0;
      let nightK = 0;
      let fbK = 0;
      let cost = 0;
      let costKnown = false;
      for (const c of cols) {
        dayK += c.day;
        nightK += c.night;
        fbK += c.fb;
        if (c.cost !== null) {
          cost += c.cost;
          costKnown = true;
        }
      }
      const pd = m.priceDay;
      const pn = m.priceNight;
      if (!costKnown && pd !== null && pn !== null && dayK + nightK > 0) {
        cost = dayK * pd + nightK * pn; // no cost statistics yet: same accounting with today's prices
        costKnown = true;
      }
      const total = dayK + nightK + fbK;

      // readout = the bar tooltip (hover, tap or arrow keys); otherwise the y-axis unit
      const read = [];
      if (sel !== null) {
        const b = rg.buckets[sel];
        const c = cols[sel];
        let lab;
        if (rg.period === 'day') lab = pad(zoneParts(b.start, tz).h) + ':00–' + pad(zoneParts(b.end, tz).h) + ':00';
        else if (rg.period === 'year') lab = monthName(lang, b.month) + ' ' + b.year;
        else lab = dtf(lang, tz, { weekday: 'short', day: 'numeric', month: 'long' }).format(b.start);
        read.push(el('b', '', c.future ? '—' : fmtKwh(lang, c.day + c.night + c.fb, loc)));
        read.push(el('span', 'lab', lab));
        if (c.has) {
          read.push(el('span', 'k d'), el('span', '', fmtK(c.day, loc)));
          read.push(el('span', 'k n'), el('span', '', fmtK(c.night, loc)));
          if (c.cost !== null) read.push(el('span', '', '· ' + fmtCost(c.cost, h)));
        } else if (c.fb > 0) {
          read.push(el('span', 'k f'), el('span', '', tr(lang, 'no_split_note')));
        }
      } else {
        read.push(el('span', '', unit));
      }
      r.statRead.replaceChildren(...read);
      setAttr(r.chart, 'aria-label', tr(lang, 'stat_aria', { p: rg.title, v: fmtKwh(lang, total, loc) }));

      const lg = (k, label, v) => {
        const s = el('span', 'lg');
        s.appendChild(el('span', 'sw2 ' + k));
        s.appendChild(el('span', '', label));
        s.appendChild(el('b', '', fmtKwh(lang, v, loc)));
        return s;
      };
      const legend = [lg('d', tr(lang, 'tariff_day'), dayK), lg('n', tr(lang, 'tariff_night'), nightK)];
      if (fbK > 0) legend.push(lg('f', tr(lang, 'no_split'), fbK));
      r.legend.replaceChildren(...legend);

      setText(r.totL1, tr(lang, 'total'));
      setText(r.totV1, fmtK(total, loc));
      setText(r.totU1, ' ' + unit);
      setText(r.totL2, tr(lang, 'cost'));
      setText(r.totV2, costKnown ? fmtCost(cost, h) : '—');

      const savings = pd !== null && pn !== null && pd > pn && nightK > 0 ? nightK * (pd - pn) : null;
      show(r.save, savings !== null);
      if (savings !== null) {
        setText(r.saveL, tr(lang, 'savings'));
        setText(r.saveV, fmtCost(savings, h));
      }
    }
  }

  /* ------------------------------------------------------------------ tile */

  const TILE_CSS = THEME_CSS + `
ha-card{height:100%;border-radius:var(--es-radius,30px);background:var(--tile-base);border:1px solid var(--glass-b)}
.t-glow{position:absolute;inset:-40% -20% auto -20%;height:150%;pointer-events:none;z-index:0;
  background:radial-gradient(closest-side,var(--glow-c,var(--glow-idle)),transparent 72%);opacity:.75}
.t-glow.on{--glow-c:var(--glow-on)}.t-glow.night{--glow-c:var(--glow-night)}.t-glow.fault{--glow-c:var(--glow-fault)}
.t-glow.off{--glow-c:var(--glow-off)}.t-glow.idle{--glow-c:var(--glow-idle)}
.empty{min-height:150px;padding:18px}
.empty .ic{width:40px;height:40px;border-radius:14px}
.empty h2{font-size:16px}
.empty p{font-size:12px;line-height:16px}
.ts{position:relative;z-index:1;height:100%;min-height:170px;padding:14px;display:flex;flex-direction:column;justify-content:space-between;gap:6px;cursor:pointer}
.ts-head{display:flex;align-items:center;justify-content:space-between;gap:8px}
.ts-head .n{font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ts-ring{position:relative;width:92px;height:92px;align-self:center;flex:none}
.ts-ring svg{position:absolute;inset:0}
.ts-ring .in{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center}
.ts-ring .v{font-size:24px;line-height:26px;font-weight:600;letter-spacing:-.6px;font-variant-numeric:tabular-nums}
.ts-ring .u{font-size:11px;color:var(--fg2)}
.ts-ring .tr{stroke:var(--ring-track)}
.ts-msg{display:flex;flex-direction:column;gap:2px;min-width:0}
.ts-msg .b{font-size:22px;line-height:26px;font-weight:600;letter-spacing:-.4px;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.ts-msg .b.red{color:var(--red)}
.ts-msg .s{font-size:13px;color:var(--fg2);font-variant-numeric:tabular-nums}
.ts-foot{display:flex;align-items:center;gap:6px;font-size:13px;color:var(--fg2);font-variant-numeric:tabular-nums;min-width:0}
.ts-foot.purple{color:var(--purple-pill)}
.ts-foot.center{justify-content:center}
.ts-foot span:last-child{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tm{position:relative;z-index:1;height:100%;min-height:170px;padding:18px}
.tm-c{height:100%;display:flex;align-items:center;gap:16px}
.tm-l{flex:1;min-width:0;display:flex;flex-direction:column;gap:6px;cursor:pointer}
.tm-st{display:inline-flex;align-items:center;gap:8px;font-size:13px;font-weight:600;color:var(--green)}
.tm-st .dot{width:8px;height:8px;border-radius:4px;background:var(--green-dot);flex:none}
.tm-kw{display:flex;align-items:baseline;gap:6px}
.tm-kw .v{font-size:46px;line-height:50px;font-weight:600;letter-spacing:-1.4px;font-variant-numeric:tabular-nums}
.tm-kw .u{font-size:17px;font-weight:500;color:var(--fg2)}
.tm-line{font-size:13px;color:var(--fg2);font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tm-a{display:flex;flex-direction:column;align-items:center;gap:4px;flex:none}
.tm-stop{width:64px;height:64px;border-radius:32px;border:1px solid var(--red-b);background:var(--red-bg);color:var(--red);display:flex;align-items:center;justify-content:center;padding:0}
.tm-plus{height:44px;padding:0;border:0;background:none;display:flex;align-items:center}
.tm-plus span{display:inline-flex;align-items:center;height:30px;padding:0 12px;border-radius:15px;background:var(--blue-bg);color:var(--blue);font-size:13px;font-weight:600;white-space:nowrap}
.tm-i{height:100%;display:flex;flex-direction:column;justify-content:space-between;gap:8px}
.tm-top{display:flex;align-items:center;justify-content:space-between;gap:10px;min-width:0}
.tm-pill{display:inline-flex;align-items:center;gap:8px;font-size:13px;font-weight:600;min-width:0}
.tm-pill span:last-child{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tm-pill .dot{width:8px;height:8px;border-radius:4px;background:var(--neutral-dot);flex:none}
.tm-pill.purple{color:var(--purple-pill)}.tm-pill.red{color:var(--red)}.tm-pill.red .dot{background:var(--red)}
.tm-pill.neutral{color:var(--neutral)}.tm-pill.green{color:var(--green)}.tm-pill.green .dot{background:var(--green-dot)}
.tm-r{font-size:13px;color:var(--fg2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:none;max-width:45%}
.tm-m{display:flex;align-items:baseline;gap:8px;min-width:0;cursor:pointer}
.tm-m .b{font-size:40px;line-height:44px;font-weight:600;letter-spacing:-1.2px;font-variant-numeric:tabular-nums;white-space:nowrap}
.tm-m.col{flex-direction:column;align-items:flex-start;gap:2px}
.tm-m .b.txt{font-size:26px;line-height:30px;letter-spacing:-.5px;overflow:hidden;text-overflow:ellipsis;max-width:100%}
.tm-m.col .s{font-size:13px;max-width:100%}
.tm-m .b.red{color:var(--red)}
.tm-m .s{font-size:15px;color:var(--fg2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.tm-b{display:flex;gap:8px}
.tm-go{flex:1;min-width:0;height:44px;border-radius:22px;border:0;background:var(--slide-knob);color:var(--slide-knob-fg);font-size:15px;font-weight:600;
  display:flex;align-items:center;justify-content:center;gap:6px;padding:0 12px}
.tm-go span:last-child{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tm-go.neutral{background:var(--chip);color:var(--fg)}
.tm-o{flex:none;width:44px;height:44px;border-radius:22px;border:0;background:var(--chip);color:var(--fg);display:flex;align-items:center;justify-content:center;padding:0}
`;

  const TILE_HTML = (uid) => `
<ha-card data-r="card" class="dark">
  <div class="t-glow idle" data-r="glow" aria-hidden="true"></div>
  <div class="empty" data-r="empty" hidden>
    <span class="ic">${svgIcon('plug', 22, 1.8)}</span>
    <h2 data-r="emptyTitle"></h2>
    <p data-r="emptyText"></p>
  </div>
  <div class="ts" data-r="small" role="button" tabindex="0" hidden>
    <div class="ts-head"><span class="n" data-r="sName">ElectroS</span><span class="ic" data-r="sIcon"></span></div>
    <div class="ts-ring" data-r="sRing" hidden>
      <svg width="92" height="92" viewBox="0 0 92 92" aria-hidden="true" focusable="false">
        <defs><linearGradient id="${uid}-t" x1="0" y1="0" x2="1" y2="1"><stop offset="0" style="stop-color:var(--grad-a)"/><stop offset="1" style="stop-color:var(--accent)"/></linearGradient></defs>
        <circle class="tr" cx="46" cy="46" r="38" fill="none" stroke-width="9"/>
        <circle data-r="sProg" cx="46" cy="46" r="38" fill="none" stroke="url(#${uid}-t)" stroke-width="9" stroke-linecap="round" stroke-dasharray="${TILE_C.toFixed(2)}" stroke-dashoffset="0" transform="rotate(-90 46 46)"/>
      </svg>
      <div class="in"><span class="v" data-r="sVal"></span><span class="u" data-r="sUnit"></span></div>
    </div>
    <div class="ts-msg" data-r="sMsg"><span class="b" data-r="sBig"></span><span class="s" data-r="sSub"></span></div>
    <div class="ts-foot" data-r="sFoot"><span class="ic" data-r="sFootIcon"></span><span data-r="sFootText"></span></div>
  </div>
  <div class="tm" data-r="medium" hidden>
    <div class="tm-c" data-r="mC">
      <div class="tm-l" data-r="mInfo1" role="button" tabindex="0">
        <div class="tm-st"><span class="dot pulse"></span><span data-r="mStatus"></span></div>
        <div class="tm-kw"><span class="v" data-r="mKw"></span><span class="u" data-r="mKwU"></span></div>
        <span class="tm-line" data-r="mLine"></span>
      </div>
      <div class="tm-a">
        <button class="tm-stop" data-r="mStop" type="button">${svgIcon('stop', 22, 2.4)}</button>
        <button class="tm-plus" data-r="mPlus" type="button"><span data-r="mPlusT"></span></button>
      </div>
    </div>
    <div class="tm-i" data-r="mI">
      <div class="tm-top"><div class="tm-pill neutral" data-r="mPill"><span class="dot" data-r="mPillDot"></span><span class="ic" data-r="mPillIcon" hidden></span><span data-r="mPillText"></span></div><span class="tm-r" data-r="mRight"></span></div>
      <div class="tm-m" data-r="mInfo2" role="button" tabindex="0"><span class="b" data-r="mBig"></span><span class="s" data-r="mSub"></span></div>
      <div class="tm-b">
        <button class="tm-go" data-r="mGo" type="button">${svgIcon('bolt', 16, 2.2)}<span data-r="mGoT"></span></button>
        <button class="tm-o" data-r="mOther" type="button">${svgIcon('clock', 18, 2)}</button>
      </div>
    </div>
  </div>
</ha-card>`;

  class ElectrosTile extends ElectrosBase {
    _isTile() {
      return true;
    }

    static getConfigElement() {
      return document.createElement('electros-tile-editor');
    }

    static getStubConfig(hass) {
      const dev = firstDevice(hass);
      const cfg = { size: 'medium', theme: 'auto' };
      if (dev) cfg.device_id = dev;
      return cfg;
    }

    getCardSize() {
      return 3;
    }

    getGridOptions() {
      return this._config.size === 'small'
        ? { columns: 6, rows: 3, min_columns: 4, min_rows: 3 }
        : { columns: 12, rows: 3, min_columns: 8, min_rows: 3 };
    }

    _rootClass() {
      return this._config.size === 'small' ? 'small' : 'medium';
    }

    _build() {
      this.shadowRoot.innerHTML = '<style>' + TILE_CSS + '</style>' + TILE_HTML(this._uid);
      const r = (this._r = collectRefs(this.shadowRoot));
      const info = (el) => {
        el.addEventListener('click', () => this._moreInfo());
        el.addEventListener('keydown', (e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            this._moreInfo();
          }
        });
      };
      info(r.small);
      info(r.mInfo1);
      info(r.mInfo2);
      r.mStop.addEventListener('click', () => {
        if (!this._m || this._busy) return;
        this._setBusy('stop');
        this._stopCharge();
        this._update();
      });
      r.mPlus.addEventListener('click', () => this._plus());
      r.mGo.addEventListener('click', () => {
        const m = this._m;
        if (!m || m.offline || this._busy) return;
        this._setBusy('start');
        this._startCharge(this._selection(m));
        this._update();
      });
      r.mOther.addEventListener('click', () => {
        const m = this._m;
        if (!m) return;
        const opts = this._config.quick_minutes.concat([0]);
        const i = opts.indexOf(this._selection(m));
        this._sel = opts[(i + 1) % opts.length];
        this._update();
      });
    }

    _empty(title, text) {
      const r = this._r;
      show(r.small, false);
      show(r.medium, false);
      show(r.empty, true);
      setText(r.emptyTitle, title);
      setText(r.emptyText, text);
      show(r.emptyText, !!text);
      setClass(r.glow, 't-glow off');
    }

    _render(m, lang, h) {
      const r = this._r;
      const loc = numLocale(h);
      const busy = this._busy ? this._busy.kind : null;
      const small = this._config.size === 'small';
      const pill = pillFor(m, lang);
      show(r.empty, false);
      show(r.small, small);
      show(r.medium, !small);
      const glow = m.charging ? 'on' : m.cat === 'fault' ? 'fault'
        : (m.cat === 'offline' || m.cat === 'unknown') ? 'off'
          : (m.heroTime || m.status === 'charging_blocked') ? 'night' : 'idle';
      setClass(r.glow, 't-glow ' + glow);
      const dev = (h.devices && h.devices[this._config.device_id]) || null;
      const name = (dev && dev.name_by_user) || 'ElectroS';
      const win = fmtHM(m.tStart) + ' → ' + fmtHM(m.tEnd);
      const kwh = fmtNum(m.session !== null ? m.session : 0, 1, loc) + ' ' + tr(lang, 'kwh');

      if (small) {
        setText(r.sName, name);
        setAttr(r.small, 'aria-label', name + ': ' + pill.text);
        show(r.sRing, m.charging);
        show(r.sMsg, !m.charging);
        let hIcon = 'plug';
        let hCls = 'grey';
        if (m.charging) {
          hIcon = 'bolt';
          hCls = 'green';
          setText(r.sVal, fmtNum(m.kw, 1, loc));
          setText(r.sUnit, tr(lang, 'kw'));
          const frac = m.frac > 0.002 ? m.frac : 0;
          setAttr(r.sProg, 'stroke-dashoffset', (TILE_C * (1 - frac)).toFixed(2));
          show(r.sProg, frac > 0);
          let foot;
          if (m.mode === 'energy') foot = this._ofTarget(m, lang, loc);
          else if (m.mode === 'timed') foot = tr(lang, 'left_d', { d: fmtDur(lang, Math.floor(m.remaining / 60)) });
          else if (m.mode === 'unlimited') foot = tr(lang, 'no_limit_l');
          else if (m.mode === 'schedule') foot = tr(lang, 'until_t', { t: fmtHM(m.tEnd) });
          else foot = kwh;
          setClass(r.sFoot, 'ts-foot center');
          setIcon(r.sFootIcon, null, 14);
          show(r.sFootIcon, false);
          setText(r.sFootText, foot);
        } else {
          let big;
          let sub = '';
          let red = false;
          if (m.status === 'waiting_for_vehicle') {
            big = tr(lang, 'st_waiting_for_vehicle');
            sub = tr(lang, 'cable_not_connected');
          } else if (m.status === 'vehicle_connected_idle') {
            big = tr(lang, 'st_vehicle_connected_idle');
            sub = m.waitingNight ? tr(lang, 'start_in', { d: fmtDur(lang, m.minsToStart) }) : tr(lang, 'ready');
            if (m.waitingNight) hCls = 'purple';
          } else if (m.status === 'charging_blocked') {
            big = tr(lang, 'blocked_short');
            sub = m.waitingNight ? tr(lang, 'start_in', { d: fmtDur(lang, m.minsToStart) }) : tr(lang, 'blocked_sub');
            hIcon = m.nightOn ? 'moon' : 'lock';
            hCls = 'purple';
          } else if (m.cat === 'fault') {
            big = tr(lang, 'st_' + m.status);
            sub = tr(lang, 'fh_' + m.status);
            red = true;
            hIcon = 'warn';
            hCls = 'red';
          } else if (m.offline) {
            big = tr(lang, 'st_offline');
            sub = Number.isFinite(m.statusSince) ? tr(lang, 'offline_since', { t: clockOf(m.statusSince, m.tz) }) : tr(lang, 'no_link');
            hIcon = 'offline';
          } else {
            big = tr(lang, 'st_unknown');
            hIcon = 'question';
          }
          setText(r.sBig, big);
          setClass(r.sBig, 'b' + (red ? ' red' : ''));
          setText(r.sSub, sub);
          show(r.sSub, !!sub);
          show(r.sFootIcon, true);
          if (m.nightOn) {
            setClass(r.sFoot, 'ts-foot purple');
            setIcon(r.sFootIcon, 'moon', 14, 2);
            setText(r.sFootText, m.weekendOn && m.weekendToday ? tr(lang, 'weekend_today') : win);
          } else {
            setClass(r.sFoot, 'ts-foot');
            setIcon(r.sFootIcon, 'clock', 14, 2);
            setText(r.sFootText, tr(lang, 'timer_off'));
          }
        }
        setClass(r.sIcon, 'ic ' + hCls);
        setIcon(r.sIcon, hIcon, 16, m.charging ? 2.2 : 2);
        return;
      }

      // medium
      show(r.mC, m.charging);
      show(r.mI, !m.charging);
      if (m.charging) {
        setText(r.mStatus, tr(lang, 'st_charging'));
        setText(r.mKw, fmtNum(m.kw, 1, loc));
        setText(r.mKwU, tr(lang, 'kw'));
        const parts = [];
        if (m.mode === 'energy') {
          // "4,2 з 10 кВт·год · ≈ 22:05 · 32 А"
          parts.push(this._ofTarget(m, lang, loc));
          parts.push(m.etaMs !== null ? tr(lang, 'approx', { v: clockOf(m.etaMs, m.tz) }) : tr(lang, 'eta_unknown'));
        } else {
          if (m.mode === 'timed') parts.push(tr(lang, 'until_t', { t: clockOf(m.boostEnd, m.tz) }));
          else if (m.mode === 'unlimited') parts.push(tr(lang, 'no_limit_l'));
          else if (m.mode === 'schedule') parts.push(tr(lang, 'until_t', { t: fmtHM(m.tEnd) }));
          parts.push(kwh);
        }
        const a = m.amps !== null && m.amps !== undefined ? m.amps : m.measured;
        if (a !== null && a !== undefined) parts.push(fmtNum(a, Number.isInteger(a) ? 0 : 1, loc) + ' ' + tr(lang, 'a'));
        setText(r.mLine, parts.join(' · '));
        setAttr(r.mInfo1, 'aria-label', name + ': ' + tr(lang, 'st_charging') + ', ' + fmtNum(m.kw, 1, loc) + ' ' + tr(lang, 'kw'));
        setAttr(r.mStop, 'aria-label', busy === 'stop' ? tr(lang, 'stopping') : tr(lang, 'stop'));
        setAttr(r.mStop, 'title', tr(lang, 'stop'));
        setDisabled(r.mStop, m.offline || busy === 'stop');
        show(r.mPlus, m.mode === 'timed' || m.mode === 'unlimited' || m.mode === 'energy');
        const pl = this._plusLabel(m, lang);
        setText(r.mPlusT, pl.text);
        setAttr(r.mPlus, 'aria-label', pl.aria);
        setDisabled(r.mPlus, m.offline);
        return;
      }

      setClass(r.mPill, 'tm-pill ' + pill.kind);
      setText(r.mPillText, pill.text);
      show(r.mPillDot, !pill.icon);
      show(r.mPillIcon, !!pill.icon);
      if (pill.icon) setIcon(r.mPillIcon, pill.icon, 14, 2);
      let right = '';
      let big;
      let sub = '';
      let txt = false;
      let red = false;
      if (m.status === 'waiting_for_vehicle') {
        right = tr(lang, 'cable_not_connected');
        if (m.timerEffective && !m.inWin) {
          big = fmtHM(m.tStart);
          sub = tr(lang, 'in_d', { d: fmtDur(lang, m.minsToStart) });
        } else {
          big = tr(lang, 'plug_in');
          txt = true;
        }
      } else if (m.heroTime) {
        right = tr(lang, 'st_vehicle_connected_idle').toLowerCase();
        big = fmtHM(m.tStart);
        sub = tr(lang, 'in_d', { d: fmtDur(lang, m.minsToStart) });
      } else if (m.status === 'charging_blocked') {
        right = tr(lang, 'st_vehicle_connected_idle').toLowerCase();
        big = tr(lang, 'blocked_big');
        sub = tr(lang, 'blocked_sub');
        txt = true;
      } else if (m.status === 'vehicle_connected_idle') {
        big = fmtNum(m.amps !== null && m.amps !== undefined ? m.amps : 0, 0, loc) + ' ' + tr(lang, 'a');
        sub = '≈ ' + fmtNum(((m.amps || 0) * (m.voltage || 230)) / 1000, 1, loc) + ' ' + tr(lang, 'kw') + ' · ' + tr(lang, 'ready');
      } else if (m.cat === 'fault') {
        big = tr(lang, 'stopped');
        sub = tr(lang, 'fh_' + m.status);
        txt = true;
        red = true;
      } else if (m.offline) {
        big = tr(lang, 'no_link');
        sub = Number.isFinite(m.statusSince) ? tr(lang, 'offline_since', { t: clockOf(m.statusSince, m.tz) }) : '';
        txt = true;
      } else {
        big = '—';
      }
      setText(r.mRight, right);
      show(r.mRight, !!right);
      setText(r.mBig, big);
      setClass(r.mInfo2, 'tm-m' + (txt ? ' col' : ''));
      setClass(r.mBig, 'b' + (txt ? ' txt' : '') + (red ? ' red' : ''));
      setText(r.mSub, sub);
      show(r.mSub, !!sub);
      setAttr(r.mInfo2, 'aria-label', name + ': ' + pill.text + (sub ? ', ' + big + ' ' + sub : ', ' + big));
      const sel = this._selection(m);
      const dur = sel > 0 ? fmtDur(lang, sel) : tr(lang, 'no_limit_l');
      setClass(r.mGo, 'tm-go' + (m.cat === 'fault' ? ' neutral' : ''));
      setText(r.mGoT, busy === 'start' ? tr(lang, 'starting') : m.cat === 'fault' ? tr(lang, 'retry')
        : sel > 0 ? tr(lang, 'charge_n', { d: fmtDur(lang, sel) }) : tr(lang, 'start'));
      setDisabled(r.mGo, m.offline || !!busy || (sel === 0 && !this._map['switch.charging']));
      setAttr(r.mOther, 'aria-label', tr(lang, 'other_time', { d: dur }));
      setAttr(r.mOther, 'title', tr(lang, 'other_time', { d: dur }));
      setDisabled(r.mOther, m.offline);
    }
  }

  /* --------------------------------------------------------------- editors */

  class ElectrosCardEditor extends HTMLElement {
    constructor() {
      super();
      this._config = {};
      this._hass = null;
      this._form = null;
    }

    _tile() {
      return false;
    }

    setConfig(config) {
      this._config = Object.assign({}, config || {});
      this._render();
    }

    set hass(h) {
      this._hass = h;
      this._render();
    }

    get hass() {
      return this._hass;
    }

    _schema(lang) {
      const schema = [
        // `filter` is the current HA form, `integration` the legacy one; both restrict the picker to our devices
        { name: 'device_id', required: true, selector: { device: { integration: DOMAIN, filter: { integration: DOMAIN } } } },
        {
          name: 'theme',
          selector: {
            select: {
              mode: 'dropdown',
              options: [
                { value: 'auto', label: tr(lang, 'theme_auto') },
                { value: 'light', label: tr(lang, 'theme_light') },
                { value: 'dark', label: tr(lang, 'theme_dark') },
              ],
            },
          },
        },
        {
          name: 'quick_minutes',
          selector: {
            select: {
              multiple: true,
              custom_value: true,
              mode: 'dropdown',
              options: QUICK_OPTIONS.map((m) => ({ value: String(m), label: fmtDur(lang, m) })),
            },
          },
        },
      ];
      if (!this._tile()) {
        schema.push({
          name: 'quick_kwh',
          selector: {
            select: {
              multiple: true,
              custom_value: true,
              mode: 'dropdown',
              options: QUICK_KWH_OPTIONS.map((k) => ({ value: String(k), label: fmtKwh(lang, k, numLocale(this._hass)) })),
            },
          },
        });
      }
      if (this._tile()) {
        schema.push({
          name: 'size',
          selector: {
            select: {
              mode: 'dropdown',
              options: [
                { value: 'small', label: tr(lang, 'size_small') },
                { value: 'medium', label: tr(lang, 'size_medium') },
              ],
            },
          },
        });
      }
      return schema;
    }

    _data() {
      const c = this._config;
      const q = Array.isArray(c.quick_minutes) ? c.quick_minutes : DEFAULT_QUICK;
      const d = {
        device_id: c.device_id || undefined,
        theme: c.theme || 'auto',
        quick_minutes: q.map((x) => String(x)),
      };
      if (!this._tile()) d.quick_kwh = normalizeKwhList(c.quick_kwh).map((x) => String(x));
      if (this._tile()) d.size = c.size === 'small' ? 'small' : 'medium';
      return d;
    }

    _render() {
      if (!this._hass) return;
      const lang = langOf(this._hass);
      if (!this._form) {
        this._form = document.createElement('ha-form');
        this._form.addEventListener('value-changed', (ev) => this._valueChanged(ev));
        this.appendChild(this._form);
      }
      this._form.hass = this._hass;
      this._form.data = this._data();
      this._form.schema = this._schema(lang);
      this._form.computeLabel = (s) => tr(lang, 'ed_' + s.name);
    }

    _valueChanged(ev) {
      if (ev.stopPropagation) ev.stopPropagation();
      const v = (ev.detail && ev.detail.value) || {};
      const cfg = Object.assign({}, this._config);
      if (v.device_id) cfg.device_id = v.device_id;
      else delete cfg.device_id;
      // the v2.0 price_per_kwh option is gone (cost comes from the backend); drop it when the card is re-saved
      delete cfg.price_per_kwh;
      cfg.theme = ['auto', 'light', 'dark'].includes(v.theme) ? v.theme : 'auto';
      const q = (Array.isArray(v.quick_minutes) ? v.quick_minutes : [])
        .map((x) => Math.round(Number(x)))
        .filter((x) => Number.isFinite(x) && x >= 1 && x <= 1440);
      cfg.quick_minutes = q.length ? Array.from(new Set(q)).sort((a, b) => a - b).slice(0, 5) : DEFAULT_QUICK.slice();
      if (!this._tile()) cfg.quick_kwh = normalizeKwhList(Array.isArray(v.quick_kwh) ? v.quick_kwh : []);
      if (this._tile()) cfg.size = v.size === 'small' ? 'small' : 'medium';
      this._config = cfg;
      fire(this, 'config-changed', { config: cfg });
    }
  }

  class ElectrosTileEditor extends ElectrosCardEditor {
    _tile() {
      return true;
    }
  }

  /* ---------------------------------------------------------- registration */

  const define = (name, cls) => {
    if (!customElements.get(name)) customElements.define(name, cls);
  };
  define('electros-card-editor', ElectrosCardEditor);
  define('electros-tile-editor', ElectrosTileEditor);
  define('electros-card', ElectrosCard);
  define('electros-tile', ElectrosTile);

  // Exposed for dev/smoke.mjs and debugging; not part of the public contract.
  ElectrosCard._internals = {
    VERSION, buildMap, firstDevice, computeModel, normalizeConfig, inWindow, fmtDur, fmtCountdown, parseHM, fmtHM, tr, langOf, pillFor,
    zonedMs, statRange, mergeStats, niceCeil, normalizeKwhList, lastErrorText, I18N,
  };

  const navLang = (() => {
    try {
      return String((document.documentElement && document.documentElement.lang) || navigator.language || 'uk').toLowerCase().split(/[-_]/)[0];
    } catch (e) {
      return 'uk';
    }
  })();
  const DESC = {
    uk: ['Зарядка ElectroS: потужність, «зарядити на N хв / N кВт·год», струм, нічний тариф, статистика день/ніч і вартість.', 'Компактна плитка ElectroS (мала або середня) зі статусом і кнопкою старт/стоп.'],
    ru: ['Зарядка ElectroS: мощность, «зарядить на N мин / N кВт·ч», ток, ночной тариф, статистика день/ночь и стоимость.', 'Компактная плитка ElectroS (маленькая или средняя) со статусом и кнопкой старт/стоп.'],
    en: ['ElectroS charger: live power, "charge for N min / N kWh", current, night rate, day/night statistics and cost.', 'Compact ElectroS tile (small or medium) with status and start/stop.'],
  }[I18N[navLang] ? navLang : 'en'];

  window.customCards = window.customCards || [];
  const register = (o) => {
    if (!window.customCards.some((c) => c && c.type === o.type)) window.customCards.push(o);
  };
  register({ type: 'electros-card', name: 'Струм', description: DESC[0], preview: true, documentationURL: DOCS_URL });
  register({ type: 'electros-tile', name: 'Струм — плитка', description: DESC[1], preview: true, documentationURL: DOCS_URL });

  // eslint-disable-next-line no-console
  console.info('%c STRUM · ELECTROS-CARD %c ' + VERSION + ' ', 'color:#04210E;background:#30D158;font-weight:700', 'color:#30D158;background:#07090C');
})();
