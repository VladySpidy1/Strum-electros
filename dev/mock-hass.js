/*
 * Mock Home Assistant `hass` object for the ElectroS card preview (dev/card-preview.html)
 * and the node smoke test (dev/smoke.mjs). Classic script: defines globalThis.ElectrosMock.
 *
 * Everything is in memory. callService() logs the call and mutates the mock state roughly the way
 * the real backend is expected to (see the shared contract v2), then pushes a new hass object.
 * callWS() answers recorder/statistics_during_period from a deterministic generator
 * (night sessions from 23:00, occasional daytime top-ups) split into the two tariff zones.
 * No network access of any kind.
 */
(function (g) {
  'use strict';

  const DOMAIN = 'electros_ev';
  const KEYS = [
    ['sensor', 'status'], ['sensor', 'configured_current'], ['sensor', 'measured_current'],
    ['sensor', 'voltage'], ['sensor', 'power'], ['sensor', 'energy'], ['sensor', 'session_energy'],
    ['sensor', 'month_energy'], ['sensor', 'boost_end'],
    ['switch', 'charging'], ['switch', 'night_timer'], ['switch', 'weekend_free'],
    ['number', 'charging_amps'], ['number', 'boost_minutes'],
    ['time', 'timer_start'], ['time', 'timer_end'],
    ['button', 'boost'], ['button', 'reset_energy'],
  ];
  /* Contract v2 additions (charge to an energy target, two-zone tariff). Devices with `legacy: true` lack them. */
  const KEYS_V2 = [
    ['number', 'boost_kwh'], ['button', 'boost_energy'],
    ['number', 'price_day'], ['number', 'price_night'],
    ['sensor', 'energy_day_tariff'], ['sensor', 'energy_night_tariff'], ['sensor', 'energy_cost'], ['sensor', 'current_price'],
  ];
  const CONFIG_KEYS = new Set(['price_day', 'price_night']);
  const CODE = {
    waiting_for_vehicle: '0', vehicle_connected_idle: '1', charging: '2', charging_fault: '3',
    charging_blocked: '4', mains_voltage_low: '5', charger_communication_fault: '6', offline: '7',
    leakage_current_fault: '16', overcurrent_fault: '32', unknown: '',
  };
  const FAULTS = ['charging_fault', 'mains_voltage_low', 'charger_communication_fault', 'leakage_current_fault', 'overcurrent_fault'];
  const HISTORY = [
    { date: '25.10', time: '22:29:57', kwh: 25.8 },
    { date: '24.10', time: '23:02:11', kwh: 12.2 },
    { date: '23.10', time: '23:00:04', kwh: 9.4 },
    { date: '20.10', time: '23:00:02', kwh: 9.1 },
    { date: '17.10', time: '23:01:40', kwh: 18.4 },
    { date: '14.10', time: '23:00:09', kwh: 14.9 },
  ];
  /* Device counter (field 31): Jan..Dec kWh; slots after the current month still hold last year's values. */
  const MONTHS = [210.4, 188.2, 160.3, 120.5, 98.1, 80.2, 75.4, 82.3, 101.7, 84.5, 140.2, 190.6];
  const PRICE_DAY = 4.32;
  const PRICE_NIGHT = 2.16;
  const ZONE_START = 23; // tariff night zone (options flow defaults) 23:00–07:00
  const ZONE_END = 7;
  const HOUR = 3600000;

  /* Scenario presets. `boost.remainingSec` is relative to "now" when the mock is created. */
  const SCENARIOS = {
    charging: {
      label: 'Charging · 30-min boost', status: 'charging', amps: 32, voltage: 231, session: 4.2,
      startedMinAgo: 34, boost: { minutes: 30, remainingSec: 27 * 60 + 14 }, night: true, weekend: false,
    },
    energy: {
      label: 'Charging · energy boost 4.2 of 10 kWh', status: 'charging', amps: 32, voltage: 231, session: 4.2,
      startedMinAgo: 34, boost: { energy: true, target: 10, delivered: 4.2 }, night: true, weekend: false,
    },
    energy_paused: {
      label: 'Energy boost · car paused (0 kW, ETA unknown)', status: 'charging', amps: 16, voltage: 229, session: 12.5,
      startedMinAgo: 95, paused: true, boost: { energy: true, target: 20, delivered: 12.5 }, night: false, weekend: false,
    },
    energy_deadline: {
      label: 'Energy boost stopped by its safety deadline', status: 'vehicle_connected_idle', amps: 16, voltage: 230,
      session: 14.1, night: false, weekend: false, lastError: 'energy_deadline',
    },
    unlimited: {
      label: 'Charging · ∞ (manual bypass)', status: 'charging', amps: 16, voltage: 229, session: 1.7,
      startedMinAgo: 26, boost: { unlimited: true }, night: true, weekend: true,
    },
    idle_night: {
      label: 'Car connected · waiting for 23:00', status: 'vehicle_connected_idle', amps: 16, voltage: 229,
      session: 25.8, night: true, weekend: false,
    },
    waiting_vehicle: {
      label: 'Waiting for vehicle', status: 'waiting_for_vehicle', amps: 16, voltage: 232, session: 25.8,
      night: true, weekend: false,
    },
    blocked: {
      label: 'Blocked by timer', status: 'charging_blocked', amps: 24, voltage: 230, session: 25.8,
      night: true, weekend: true,
    },
    fault: {
      label: 'Fault · leakage current', status: 'leakage_current_fault', amps: 16, voltage: 228, session: 3.1,
      night: false, weekend: false,
    },
    offline: {
      label: 'Offline', status: 'offline', amps: 16, voltage: null, session: 25.8, night: true, weekend: false,
      offlineMinAgo: 14,
    },
  };

  const pad = (n) => String(n).padStart(2, '0');
  const iso = (ms) => new Date(ms).toISOString();
  const delay = (ms) => new Promise((res) => setTimeout(res, ms));
  const r2 = (v) => Math.round(v * 100) / 100;
  const r3 = (v) => Math.round(v * 1000) / 1000;

  /* ------------------------------------------------ tariff + statistics generator */

  const isNightHour = (h) => h >= ZONE_START || h < ZONE_END;
  const zoneAt = (ms) => (isNightHour(new Date(ms).getHours()) ? 'night' : 'day');
  const priceAt = (ms) => (zoneAt(ms) === 'night' ? PRICE_NIGHT : PRICE_DAY);

  function rnd(n) {
    const x = Math.sin(n * 12.9898 + 78.233) * 43758.5453;
    return x - Math.floor(x);
  }

  function dayIndex(ms) {
    const d = new Date(ms);
    return Math.round(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) / 86400000);
  }

  /* kWh charged in the local hour starting at `ms`, split by tariff zone. Deterministic per hour. */
  function hourKwh(ms, seed) {
    const d = new Date(ms);
    const h = d.getHours();
    const D = dayIndex(ms);
    let kwh = 0;
    const nightOf = h >= ZONE_START ? D : h < ZONE_END ? D - 1 : null;
    if (nightOf !== null && rnd(nightOf + seed) < 0.62) {
      const hours = 1 + Math.floor(rnd(nightOf * 1.7 + seed) * 5); // 1..5 h from 23:00
      const k = h >= ZONE_START ? h - ZONE_START : h + (24 - ZONE_START);
      if (k < hours) kwh = 7.1 * (0.86 + 0.14 * rnd(ms / HOUR)) * (k === hours - 1 ? 0.55 : 1);
    }
    if (!isNightHour(h) && rnd(D * 3.3 + seed) < 0.42) {
      const st = 12 + Math.floor(rnd(D * 5.1 + seed) * 7); // daytime top-up 12..18
      const len = 1 + Math.floor(rnd(D * 7.7 + seed) * 3);
      if (h >= st && h < st + len) kwh = 7.1 * (0.8 + 0.2 * rnd(ms / HOUR + 1));
    }
    return isNightHour(h) ? { day: 0, night: kwh } : { day: kwh, night: 0 };
  }

  function sumHours(from, to, seed) {
    const out = { day: 0, night: 0, cost: 0 };
    for (let t = from; t < to; t += HOUR) {
      const k = hourKwh(t, seed);
      out.day += k.day;
      out.night += k.night;
      out.cost += k.day * PRICE_DAY + k.night * PRICE_NIGHT;
    }
    return out;
  }

  function floorHour(ms) {
    const d = new Date(ms);
    d.setMinutes(0, 0, 0);
    return d.getTime();
  }

  function localMidnight(ms) {
    const d = new Date(ms);
    d.setHours(0, 0, 0, 0);
    return d.getTime();
  }

  /* Rows as HA returns them: one per period since the statistics exist, change may be 0. */
  function statRows(dev, period, start, end) {
    const seed = dev.seed;
    const inst = dev.installedAt;
    const last = floorHour(Date.now()); // only complete hours are compiled
    const rows = [];
    const bucket = (bs, be) => {
      const from = Math.max(bs, inst);
      const to = Math.min(be, last);
      if (to <= from) return;
      const s = sumHours(from, to, seed);
      rows.push({ start: bs, end: be, day: r3(s.day), night: r3(s.night), cost: r2(s.cost) });
    };
    if (period === 'hour') {
      for (let t = floorHour(start); t < end; t += HOUR) bucket(t, t + HOUR);
    } else if (period === 'day') {
      for (let t = localMidnight(start); t < end;) {
        const d = new Date(t);
        const next = new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1).getTime();
        bucket(t, next);
        t = next;
      }
    } else if (period === 'month') {
      const s0 = new Date(start);
      for (let d = new Date(s0.getFullYear(), s0.getMonth(), 1); d.getTime() < end; d = new Date(d.getFullYear(), d.getMonth() + 1, 1)) {
        bucket(d.getTime(), new Date(d.getFullYear(), d.getMonth() + 1, 1).getTime());
      }
    } else {
      throw Object.assign(new Error('mock: unsupported period ' + period), { code: 'invalid_format' });
    }
    return rows;
  }

  /* ------------------------------------------------------------------ the mock */

  function createMock(opts) {
    const o = opts || {};
    const lang = o.lang || 'uk';
    const latency = o.latency === undefined ? 350 : o.latency;
    let tz;
    try {
      tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    } catch (e) {
      tz = 'Europe/Kyiv';
    }
    const H = {
      states: {},
      entities: {},
      devices: {},
      areas: { garage: { area_id: 'garage', name: o.areaName || { uk: 'Гараж', ru: 'Гараж', en: 'Garage' }[lang] || 'Гараж' } },
    };
    const devs = {};
    const byEntity = {};
    const listeners = new Set();
    const logListeners = new Set();
    const log = [];
    const wsLog = [];
    let wsFailure = null;
    let hass = null;

    const E = (dev, domain, key) => domain + '.electros_' + dev.slug + '_' + key;
    const st = (dev, domain, key) => H.states[E(dev, domain, key)];
    const has = (dev, domain, key) => !!H.entities[E(dev, domain, key)];
    const num = (dev, domain, key) => {
      const s = st(dev, domain, key);
      const v = s ? parseFloat(s.state) : NaN;
      return Number.isFinite(v) ? v : null;
    };

    function put(id, state, attrs, opts2) {
      const old = H.states[id];
      const t = iso(Date.now());
      const s = String(state);
      const next = {
        entity_id: id,
        state: s,
        attributes: Object.assign({}, old ? old.attributes : {}, attrs || {}),
        last_changed: old && old.state === s ? old.last_changed : t,
        last_updated: t,
        context: { id: 'mock', parent_id: null, user_id: null },
      };
      if (opts2 && opts2.lastChanged) next.last_changed = opts2.lastChanged;
      H.states = Object.assign({}, H.states, { [id]: next });
    }

    /* Only touches entities the device has (legacy devices lack the v2 ones). */
    function putIf(dev, domain, key, state, attrs) {
      if (has(dev, domain, key)) put(E(dev, domain, key), state, attrs);
    }

    const NO_BOOST = { active: false, unlimited: false, minutes: 0, mode: null, target_kwh: null, delivered_kwh: null, deadline: null };

    function addDevice(spec) {
      const sc = SCENARIOS[spec.scenario];
      if (!sc) throw new Error('unknown scenario ' + spec.scenario);
      const dev = Object.assign({ slug: spec.id.replace(/[^a-z0-9]/gi, '_').toLowerCase() }, spec, { sc });
      const t = Date.now();
      dev.seed = spec.seed === undefined ? 7 : spec.seed;
      dev.installedAt = localMidnight(t - (spec.installedDaysAgo === undefined ? 75 : spec.installedDaysAgo) * 86400000);
      devs[spec.id] = dev;
      H.devices[spec.id] = {
        id: spec.id,
        name: 'ElectroS ' + (spec.login || spec.id),
        name_by_user: spec.name_by_user || null,
        area_id: spec.area_id === undefined ? 'garage' : spec.area_id,
        identifiers: [[DOMAIN, spec.login || spec.id]],
        manufacturer: 'ElectroS',
        model: 'EV Charger',
        config_entries: ['entry_' + spec.id],
      };
      const ents = Object.assign({}, H.entities);
      for (const [domain, key] of spec.legacy ? KEYS : KEYS.concat(KEYS_V2)) {
        const id = E(dev, domain, key);
        ents[id] = {
          entity_id: id, device_id: spec.id, translation_key: key, platform: DOMAIN,
          name: null, hidden: false, entity_category: CONFIG_KEYS.has(key) ? 'config' : null, labels: [],
        };
        byEntity[id] = { dev, domain, key };
      }
      H.entities = ents;

      const charging = sc.status === 'charging';
      const flowing = charging && !sc.paused;
      const v = sc.voltage;
      const started = charging ? t - sc.startedMinAgo * 60000 : t - 3 * 86400000;
      put(E(dev, 'sensor', 'status'), sc.status, { status_code: CODE[sc.status], device_class: 'enum' },
        sc.offlineMinAgo ? { lastChanged: iso(t - sc.offlineMinAgo * 60000) } : undefined);
      put(E(dev, 'sensor', 'configured_current'), sc.amps, { unit_of_measurement: 'A' });
      put(E(dev, 'sensor', 'measured_current'), flowing ? sc.amps : 0, { unit_of_measurement: 'A' });
      put(E(dev, 'sensor', 'voltage'), v === null ? 'unavailable' : v,
        v === null ? {} : { unit_of_measurement: 'V', min: v - 9, max: v + 6, avg: v - 2 });
      put(E(dev, 'sensor', 'power'), flowing ? Math.round(sc.amps * v) : 0, { unit_of_measurement: 'W' });
      put(E(dev, 'sensor', 'energy'), 1234.567, { unit_of_measurement: 'kWh' });
      const sess = { unit_of_measurement: 'kWh', started_at: iso(started), history: HISTORY.slice() };
      if (!spec.legacy) sess.cost = r2(sc.session * priceAt(started));
      put(E(dev, 'sensor', 'session_energy'), sc.session, sess);
      const curMonth = new Date(t).getMonth() + 1;
      put(E(dev, 'sensor', 'month_energy'), MONTHS[curMonth - 1], { unit_of_measurement: 'kWh', months: MONTHS.slice(), current_month: curMonth });
      const kw = flowing ? (sc.amps * v) / 1000 : 0;
      if (sc.boost && sc.boost.unlimited) {
        put(E(dev, 'sensor', 'boost_end'), 'unknown', Object.assign({ device_class: 'timestamp' }, NO_BOOST,
          { active: true, unlimited: true, mode: 'unlimited', last_error: null }));
      } else if (sc.boost && sc.boost.energy) {
        const left = sc.boost.target - sc.boost.delivered;
        put(E(dev, 'sensor', 'boost_end'), kw > 0 ? iso(t + (left / kw) * HOUR) : 'unknown', Object.assign({ device_class: 'timestamp' }, NO_BOOST, {
          active: true, mode: 'energy', target_kwh: sc.boost.target, delivered_kwh: sc.boost.delivered,
          deadline: iso(started + (sc.boost.target / (sc.amps * 0.23)) * 1.5 * HOUR + 30 * 60000), last_error: null,
        }));
      } else if (sc.boost) {
        put(E(dev, 'sensor', 'boost_end'), iso(t + sc.boost.remainingSec * 1000), Object.assign({ device_class: 'timestamp' }, NO_BOOST,
          { active: true, minutes: sc.boost.minutes, mode: 'time', last_error: null }));
      } else {
        put(E(dev, 'sensor', 'boost_end'), 'unknown', Object.assign({ device_class: 'timestamp' }, NO_BOOST, { last_error: sc.lastError || null }));
      }
      put(E(dev, 'switch', 'charging'), charging ? 'on' : 'off');
      put(E(dev, 'switch', 'night_timer'), sc.night ? 'on' : 'off');
      put(E(dev, 'switch', 'weekend_free'), sc.weekend ? 'on' : 'off');
      put(E(dev, 'number', 'charging_amps'), sc.amps, { min: 6, max: 32, step: 2, unit_of_measurement: 'A' });
      put(E(dev, 'number', 'boost_minutes'), 30, { min: 5, max: 480, step: 5, unit_of_measurement: 'min' });
      put(E(dev, 'time', 'timer_start'), '23:00:00');
      put(E(dev, 'time', 'timer_end'), '07:00:00');
      put(E(dev, 'button', 'boost'), 'unknown');
      put(E(dev, 'button', 'reset_energy'), 'unknown');
      if (!spec.legacy) {
        const cur = o.currency || 'UAH';
        put(E(dev, 'number', 'boost_kwh'), 10, { min: 1, max: 80, step: 1, unit_of_measurement: 'kWh' });
        put(E(dev, 'button', 'boost_energy'), 'unknown');
        put(E(dev, 'number', 'price_day'), PRICE_DAY, { min: 0, max: 100, step: 0.01, unit_of_measurement: cur + '/kWh' });
        put(E(dev, 'number', 'price_night'), PRICE_NIGHT, { min: 0, max: 100, step: 0.01, unit_of_measurement: cur + '/kWh' });
        const tot = spec.stats === 'none' ? { day: 0, night: 0, cost: 0 } : sumHours(dev.installedAt, floorHour(t), dev.seed);
        put(E(dev, 'sensor', 'energy_day_tariff'), r3(tot.day), { unit_of_measurement: 'kWh', device_class: 'energy', state_class: 'total_increasing' });
        put(E(dev, 'sensor', 'energy_night_tariff'), r3(tot.night), { unit_of_measurement: 'kWh', device_class: 'energy', state_class: 'total_increasing' });
        put(E(dev, 'sensor', 'energy_cost'), r2(tot.cost), { unit_of_measurement: cur, device_class: 'monetary', state_class: 'total' });
        put(E(dev, 'sensor', 'current_price'), priceAt(t), { unit_of_measurement: cur + '/kWh', zone: zoneAt(t) });
      }
      return dev;
    }

    function canStart(dev) {
      const status = st(dev, 'sensor', 'status').state;
      if (['waiting_for_vehicle', 'offline', 'unknown'].includes(status) || CODE[status] === undefined || FAULTS.includes(status)) {
        emitLog({ note: 'mock: charger cannot start while "' + status + '"' });
        return false;
      }
      return true;
    }

    /* status/power/session/switch for a start; returns the resulting power in kW */
    function beginCharging(dev, amps) {
      const now = Date.now();
      const was = st(dev, 'sensor', 'status').state === 'charging';
      if (amps) {
        put(E(dev, 'number', 'charging_amps'), amps);
        put(E(dev, 'sensor', 'configured_current'), amps);
      }
      const a = num(dev, 'number', 'charging_amps') || 16;
      const v = num(dev, 'sensor', 'voltage') || 230;
      put(E(dev, 'sensor', 'status'), 'charging', { status_code: '2' });
      put(E(dev, 'sensor', 'power'), Math.round(a * v));
      put(E(dev, 'sensor', 'measured_current'), a);
      if (!was) {
        const sess = { started_at: iso(now) };
        if (!dev.legacy) sess.cost = 0;
        put(E(dev, 'sensor', 'session_energy'), 0, sess);
      }
      put(E(dev, 'switch', 'charging'), 'on');
      return { now, kw: (a * v) / 1000, amps: a };
    }

    function startCharging(dev, minutes) {
      if (!canStart(dev)) return;
      const { now } = beginCharging(dev);
      // charge_for while charging replaces the boost; turn_on (∞) while a boost runs = unlimited bypass
      if (minutes > 0) {
        put(E(dev, 'sensor', 'boost_end'), iso(now + minutes * 60000), Object.assign({}, NO_BOOST,
          { active: true, minutes, mode: 'time', last_error: null }));
      } else {
        put(E(dev, 'sensor', 'boost_end'), 'unknown', Object.assign({}, NO_BOOST,
          { active: true, unlimited: true, mode: 'unlimited', last_error: null }));
      }
    }

    function energyEnd(now, left, kw) {
      return kw > 0 ? iso(now + (left / kw) * HOUR) : 'unknown';
    }

    function startEnergy(dev, kwh, amps) {
      if (!canStart(dev)) return;
      const { now, kw, amps: a } = beginCharging(dev, amps);
      dev.boostStart = now;
      put(E(dev, 'sensor', 'boost_end'), energyEnd(now, kwh, kw), Object.assign({}, NO_BOOST, {
        active: true, mode: 'energy', target_kwh: kwh, delivered_kwh: 0,
        deadline: iso(now + (kwh / (a * 0.23)) * 1.5 * HOUR + 30 * 60000), last_error: null,
      }));
    }

    function stopCharging(dev) {
      const status = st(dev, 'sensor', 'status').state;
      if (status === 'charging') {
        const kwh = num(dev, 'sensor', 'session_energy') || 0;
        const d = new Date();
        const sA = st(dev, 'sensor', 'session_energy').attributes;
        const hist = Array.isArray(sA.history) ? sA.history.slice() : [];
        if (kwh > 0) {
          hist.unshift({
            date: pad(d.getDate()) + '.' + pad(d.getMonth() + 1),
            time: pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds()),
            kwh: Math.round(kwh * 10) / 10,
          });
        }
        put(E(dev, 'sensor', 'session_energy'), kwh, { history: hist.slice(0, 50) });
        put(E(dev, 'sensor', 'status'), 'vehicle_connected_idle', { status_code: '1' });
      }
      put(E(dev, 'sensor', 'power'), 0);
      put(E(dev, 'sensor', 'measured_current'), 0);
      put(E(dev, 'sensor', 'boost_end'), 'unknown', NO_BOOST);
      put(E(dev, 'switch', 'charging'), 'off');
    }

    function boostAttrs(dev) {
      return st(dev, 'sensor', 'boost_end').attributes;
    }

    function extendCharging(dev, minutes) {
      const b = st(dev, 'sensor', 'boost_end');
      const end = Date.parse(b.state);
      if (b.attributes.active && b.attributes.mode !== 'energy' && !b.attributes.unlimited && Number.isFinite(end) && end > Date.now()) {
        put(E(dev, 'sensor', 'boost_end'), iso(end + minutes * 60000), { minutes: (b.attributes.minutes || 0) + minutes });
      } else {
        startCharging(dev, minutes);
      }
    }

    function extendEnergy(dev, kwh) {
      const a = boostAttrs(dev);
      if (a.active && a.mode === 'energy' && st(dev, 'sensor', 'status').state === 'charging') {
        const target = r3((a.target_kwh || 0) + kwh);
        const kw = (num(dev, 'sensor', 'power') || 0) / 1000;
        const amps = num(dev, 'number', 'charging_amps') || 16;
        put(E(dev, 'sensor', 'boost_end'), energyEnd(Date.now(), target - (a.delivered_kwh || 0), kw), {
          target_kwh: target,
          deadline: iso((dev.boostStart || Date.now()) + (target / (amps * 0.23)) * 1.5 * HOUR + 30 * 60000),
        });
      } else {
        startEnergy(dev, kwh);
      }
    }

    function snapshot() {
      hass = {
        states: H.states,
        entities: H.entities,
        devices: H.devices,
        areas: H.areas,
        language: lang,
        locale: { language: lang, number_format: 'language', time_format: '24', time_zone: 'server', first_weekday: 'monday' },
        themes: { darkMode: o.darkMode !== false, theme: 'default', themes: {} },
        selectedTheme: null,
        config: { time_zone: tz, currency: o.currency || 'UAH', unit_system: { length: 'km', temperature: '°C' }, version: '2026.10.0', components: [DOMAIN, 'recorder'] },
        user: { id: 'preview', name: 'Preview', is_admin: true },
        connection: null,
        callService,
        callWS,
        localize: (k) => k,
        formatEntityState: (s) => (s ? s.state : ''),
      };
      return hass;
    }

    function push() {
      snapshot();
      for (const fn of listeners) {
        try {
          fn(hass);
        } catch (e) {
          // eslint-disable-next-line no-console
          console.error(e);
        }
      }
    }

    function emitLog(entry) {
      const e = Object.assign({ at: Date.now() }, entry);
      log.push(e);
      for (const fn of logListeners) fn(e);
    }

    function deviceFor(data) {
      if (data && data.device_id && devs[data.device_id]) return devs[data.device_id];
      const id = data && (Array.isArray(data.entity_id) ? data.entity_id[0] : data.entity_id);
      return id && byEntity[id] ? byEntity[id].dev : null;
    }

    function apply(domain, service, data) {
      const dev = deviceFor(data);
      if (!dev) throw new Error('mock: no such device/entity ' + JSON.stringify(data));
      const ref = data && data.entity_id ? byEntity[data.entity_id] : null;
      if (domain === DOMAIN) {
        if (service === 'charge_for') startCharging(dev, Math.max(1, Math.round(data.minutes)));
        else if (service === 'charge_energy') {
          const k = Number(data.kwh);
          if (!(k >= 0.5 && k <= 100)) throw new Error('mock: kwh must be 0.5..100');
          startEnergy(dev, k, data.amps);
        } else if (service === 'extend_charge') {
          const hasMin = data.minutes !== undefined && data.minutes !== null;
          const hasKwh = data.kwh !== undefined && data.kwh !== null;
          if (hasMin === hasKwh) throw new Error('mock: extend_charge needs exactly one of minutes / kwh');
          if (hasKwh) extendEnergy(dev, Number(data.kwh));
          else extendCharging(dev, Math.max(1, Math.round(data.minutes)));
        } else if (service === 'stop_charging') stopCharging(dev);
        else if (service === 'set_timer') {
          if (data.start) put(E(dev, 'time', 'timer_start'), data.start.slice(0, 5) + ':00');
          if (data.end) put(E(dev, 'time', 'timer_end'), data.end.slice(0, 5) + ':00');
          if (typeof data.enabled === 'boolean') put(E(dev, 'switch', 'night_timer'), data.enabled ? 'on' : 'off');
        } else throw new Error('mock: unknown service ' + service);
        return;
      }
      if (!ref) throw new Error('mock: entity_id required');
      const id = data.entity_id;
      if (domain === 'switch') {
        const on = H.states[id].state === 'on';
        const want = service === 'turn_on' ? true : service === 'turn_off' ? false : !on;
        if (ref.key === 'charging') {
          if (want) startCharging(dev, 0);
          else stopCharging(dev);
        } else {
          put(id, want ? 'on' : 'off');
        }
      } else if (domain === 'number' && service === 'set_value') {
        const val = Number(data.value);
        put(id, val);
        if (ref.key === 'charging_amps') {
          put(E(dev, 'sensor', 'configured_current'), val);
          if (st(dev, 'sensor', 'status').state === 'charging') {
            put(E(dev, 'sensor', 'power'), Math.round(val * (num(dev, 'sensor', 'voltage') || 230)));
            put(E(dev, 'sensor', 'measured_current'), val);
          }
        }
        if (ref.key === 'price_day' || ref.key === 'price_night') {
          const zone = zoneAt(Date.now());
          if ((zone === 'day') === (ref.key === 'price_day')) putIf(dev, 'sensor', 'current_price', val);
        }
      } else if (domain === 'time' && service === 'set_value') {
        const t = String(data.time || '');
        put(id, t.length === 5 ? t + ':00' : t);
      } else if (domain === 'button' && service === 'press') {
        if (ref.key === 'reset_energy') put(E(dev, 'sensor', 'energy'), 0);
        if (ref.key === 'boost') startCharging(dev, num(dev, 'number', 'boost_minutes') || 30);
        if (ref.key === 'boost_energy') startEnergy(dev, num(dev, 'number', 'boost_kwh') || 10);
      } else {
        throw new Error('mock: unsupported ' + domain + '.' + service);
      }
    }

    async function callService(domain, service, data) {
      emitLog({ domain, service, data: JSON.parse(JSON.stringify(data || {})) });
      if (latency) await delay(latency);
      apply(domain, service, data || {});
      push();
    }

    /* hass.callWS: recorder/statistics_during_period only (types: ["change"]). */
    async function callWS(msg) {
      const m = JSON.parse(JSON.stringify(msg || {}));
      wsLog.push(m);
      emitLog({ ws: m.type, msg: m });
      if (latency) await delay(latency);
      if (wsFailure) throw wsFailure;
      if (m.type !== 'recorder/statistics_during_period') {
        throw Object.assign(new Error('Unknown command.'), { code: 'unknown_command' });
      }
      const start = Date.parse(m.start_time);
      const end = m.end_time ? Date.parse(m.end_time) : Date.now();
      const out = {};
      for (const id of m.statistic_ids || []) {
        const ref = byEntity[id];
        if (!ref || ref.dev.stats === 'none') continue;
        const field = { energy_day_tariff: 'day', energy_night_tariff: 'night', energy_cost: 'cost' }[ref.key];
        if (!field) continue;
        const rows = statRows(ref.dev, m.period, start, end);
        if (rows.length) out[id] = rows.map((r) => ({ start: r.start, end: r.end, change: r[field] }));
      }
      return out;
    }

    /* Simulated backend clock: boosts end by themselves, energy grows and is split by tariff zone. */
    let clock = null;
    let ticks = 0;
    function inWindowNow(dev) {
      const p = (k) => {
        const s = st(dev, 'time', k);
        const mm = s ? /^(\d{1,2}):(\d{2})/.exec(s.state) : null;
        return mm ? Number(mm[1]) * 60 + Number(mm[2]) : null;
      };
      const s = p('timer_start');
      const e = p('timer_end');
      const d = new Date();
      const n = d.getHours() * 60 + d.getMinutes();
      if (s === null || e === null || s === e) return true;
      return s < e ? n >= s && n < e : n >= s || n < e;
    }

    function tickDevice(dev, id) {
      if (st(dev, 'sensor', 'status').state !== 'charging') return false;
      const b = st(dev, 'sensor', 'boost_end');
      const a = b.attributes;
      const now = Date.now();
      const end = Date.parse(b.state);
      if (a.active && a.mode === 'energy' && a.deadline && Date.parse(a.deadline) <= now) {
        stopCharging(dev);
        put(E(dev, 'sensor', 'boost_end'), 'unknown', { last_error: 'energy_deadline' });
        emitLog({ note: 'mock: energy boost on ' + id + ' hit its safety deadline' });
        return true;
      }
      if (a.active && a.mode !== 'energy' && !a.unlimited && Number.isFinite(end) && end <= now) {
        // Q8: a timed boost that ends inside the (restored) night window keeps charging on schedule
        if (st(dev, 'switch', 'night_timer').state === 'on' && inWindowNow(dev)) {
          put(E(dev, 'sensor', 'boost_end'), 'unknown', NO_BOOST);
          emitLog({ note: 'mock: boost on ' + id + ' finished inside the night window, schedule continues' });
        } else {
          stopCharging(dev);
          emitLog({ note: 'mock: boost on ' + id + ' finished, timer restored' });
        }
        return true;
      }
      if (ticks % 10 !== 0) return false;
      const kw = (num(dev, 'sensor', 'power') || 0) / 1000;
      const add = (kw * 10) / 3600;
      const price = priceAt(now);
      put(E(dev, 'sensor', 'session_energy'), r2((num(dev, 'sensor', 'session_energy') || 0) + add),
        dev.legacy ? undefined : { cost: r2((st(dev, 'sensor', 'session_energy').attributes.cost || 0) + add * price) });
      put(E(dev, 'sensor', 'energy'), r3((num(dev, 'sensor', 'energy') || 0) + add));
      if (!dev.legacy) {
        const zk = zoneAt(now) === 'night' ? 'energy_night_tariff' : 'energy_day_tariff';
        putIf(dev, 'sensor', zk, r3((num(dev, 'sensor', zk) || 0) + add));
        putIf(dev, 'sensor', 'energy_cost', r2((num(dev, 'sensor', 'energy_cost') || 0) + add * price));
        putIf(dev, 'sensor', 'current_price', price, { zone: zoneAt(now) });
      }
      if (a.active && a.mode === 'energy') {
        const delivered = r3((a.delivered_kwh || 0) + add);
        if (delivered >= (a.target_kwh || 0)) {
          // energy boosts always stop at the target (then the backend reconciles the timer)
          stopCharging(dev);
          emitLog({ note: 'mock: energy boost on ' + id + ' reached ' + a.target_kwh + ' kWh, stopped' });
        } else {
          put(E(dev, 'sensor', 'boost_end'), energyEnd(now, a.target_kwh - delivered, kw), { delivered_kwh: delivered });
        }
      }
      return true;
    }

    function startClock() {
      if (clock) return;
      clock = setInterval(() => {
        ticks += 1;
        let changed = false;
        for (const id in devs) if (tickDevice(devs[id], id)) changed = true;
        if (changed) push();
      }, 1000);
    }

    function stopClock() {
      if (clock) clearInterval(clock);
      clock = null;
    }

    for (const spec of o.devices || []) addDevice(spec);
    snapshot();

    return {
      get hass() {
        return hass;
      },
      H,
      log,
      wsLog,
      entityId: (deviceId, domain, key) => E(devs[deviceId], domain, key),
      subscribe(fn) {
        listeners.add(fn);
        return () => listeners.delete(fn);
      },
      onLog(fn) {
        logListeners.add(fn);
        return () => logListeners.delete(fn);
      },
      /* Directly change one entity (tests): pushes a new hass. */
      setState(id, state, attrs) {
        put(id, state, attrs);
        push();
      },
      patchDevice(id, patch) {
        H.devices = Object.assign({}, H.devices, { [id]: Object.assign({}, H.devices[id], patch) });
        push();
      },
      /* Make every following callWS reject with `err` (null = back to normal). */
      failWS(err) {
        wsFailure = err || null;
      },
      /* Generator access for tests: expected per-bucket values. */
      statRows: (deviceId, period, start, end) => statRows(devs[deviceId], period, start, end),
      push,
      startClock,
      stopClock,
    };
  }

  /* Time travel for previews/tests: makes `new Date()` / Date.now() start at HH:MM today (local). */
  function installFakeNow(hhmm) {
    const mm = /^(\d{1,2}):(\d{2})$/.exec(String(hhmm || ''));
    if (!mm || g.Date.__electrosFake) return 0;
    const RealDate = g.Date;
    const target = new RealDate();
    target.setHours(Number(mm[1]), Number(mm[2]), 0, 0);
    const offset = target.getTime() - RealDate.now();
    class FakeDate extends RealDate {
      constructor(...a) {
        if (a.length === 0) super(RealDate.now() + offset);
        else super(...a);
      }

      static now() {
        return RealDate.now() + offset;
      }
    }
    FakeDate.__electrosFake = true;
    g.Date = FakeDate;
    return offset;
  }

  g.ElectrosMock = { createMock, installFakeNow, SCENARIOS, KEYS, KEYS_V2, DOMAIN, PRICE_DAY, PRICE_NIGHT };
})(typeof globalThis !== 'undefined' ? globalThis : window);
