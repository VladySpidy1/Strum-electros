/*
 * Mock Home Assistant `hass` object for the ElectroS card preview (dev/card-preview.html)
 * and the node smoke test (dev/smoke.mjs). Classic script: defines globalThis.ElectrosMock.
 *
 * Everything is in memory. callService() logs the call and mutates the mock state roughly the way
 * the real backend is expected to (see the shared contract), then pushes a new hass object.
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
  const CODE = {
    waiting_for_vehicle: '0', vehicle_connected_idle: '1', charging: '2', charging_fault: '3',
    charging_blocked: '4', mains_voltage_low: '5', charger_communication_fault: '6', offline: '7',
    leakage_current_fault: '16', overcurrent_fault: '32', unknown: '',
  };
  const HISTORY = [
    { date: '25.10', time: '22:29:57', kwh: 25.8 },
    { date: '24.10', time: '23:02:11', kwh: 12.2 },
    { date: '23.10', time: '23:00:04', kwh: 9.4 },
    { date: '20.10', time: '23:00:02', kwh: 9.1 },
    { date: '17.10', time: '23:01:40', kwh: 18.4 },
    { date: '14.10', time: '23:00:09', kwh: 14.9 },
  ];
  const MONTHS = [210.4, 188.2, 160.3, 120.5, 98.1, 80.2, 75.4, 82.3, 101.7, 84.5, 0, 0];

  /* Scenario presets. `boost.remainingSec` is relative to "now" when the mock is created. */
  const SCENARIOS = {
    charging: {
      label: 'Charging · 30-min boost', status: 'charging', amps: 32, voltage: 231, session: 4.2,
      startedMinAgo: 34, boost: { minutes: 30, remainingSec: 27 * 60 + 14 }, night: true, weekend: false,
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
    let hass = null;

    const E = (dev, domain, key) => domain + '.electros_' + dev.slug + '_' + key;
    const st = (dev, domain, key) => H.states[E(dev, domain, key)];
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

    function addDevice(spec) {
      const sc = SCENARIOS[spec.scenario];
      if (!sc) throw new Error('unknown scenario ' + spec.scenario);
      const dev = Object.assign({ slug: spec.id.replace(/[^a-z0-9]/gi, '_').toLowerCase() }, spec, { sc });
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
      for (const [domain, key] of KEYS) {
        const id = E(dev, domain, key);
        ents[id] = {
          entity_id: id, device_id: spec.id, translation_key: key, platform: DOMAIN,
          name: null, hidden: false, entity_category: null, labels: [],
        };
        byEntity[id] = { dev, domain, key };
      }
      H.entities = ents;

      const t = Date.now();
      const charging = sc.status === 'charging';
      const v = sc.voltage;
      put(E(dev, 'sensor', 'status'), sc.status, { status_code: CODE[sc.status], device_class: 'enum' },
        sc.offlineMinAgo ? { lastChanged: iso(t - sc.offlineMinAgo * 60000) } : undefined);
      put(E(dev, 'sensor', 'configured_current'), sc.amps, { unit_of_measurement: 'A' });
      put(E(dev, 'sensor', 'measured_current'), charging ? sc.amps : 0, { unit_of_measurement: 'A' });
      put(E(dev, 'sensor', 'voltage'), v === null ? 'unavailable' : v,
        v === null ? {} : { unit_of_measurement: 'V', min: v - 9, max: v + 6, avg: v - 2 });
      put(E(dev, 'sensor', 'power'), charging ? Math.round(sc.amps * v) : 0, { unit_of_measurement: 'W' });
      put(E(dev, 'sensor', 'energy'), 1234.567, { unit_of_measurement: 'kWh' });
      put(E(dev, 'sensor', 'session_energy'), sc.session, {
        unit_of_measurement: 'kWh',
        started_at: charging ? iso(t - sc.startedMinAgo * 60000) : iso(t - 3 * 86400000),
        history: HISTORY.slice(),
      });
      put(E(dev, 'sensor', 'month_energy'), 84.5, { unit_of_measurement: 'kWh', months: MONTHS.slice(), current_month: 10 });
      if (sc.boost && sc.boost.unlimited) {
        put(E(dev, 'sensor', 'boost_end'), 'unknown', { device_class: 'timestamp', active: true, unlimited: true, minutes: 0 });
      } else if (sc.boost) {
        put(E(dev, 'sensor', 'boost_end'), iso(t + sc.boost.remainingSec * 1000),
          { device_class: 'timestamp', active: true, unlimited: false, minutes: sc.boost.minutes });
      } else {
        put(E(dev, 'sensor', 'boost_end'), 'unknown', { device_class: 'timestamp', active: false, unlimited: false, minutes: 0 });
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
      return dev;
    }

    function startCharging(dev, minutes) {
      const status = st(dev, 'sensor', 'status').state;
      if (['waiting_for_vehicle', 'offline', 'unknown'].includes(status) || CODE[status] === undefined
        || ['charging_fault', 'mains_voltage_low', 'charger_communication_fault', 'leakage_current_fault', 'overcurrent_fault'].includes(status)) {
        emitLog({ note: 'mock: charger cannot start while "' + status + '"' });
        return;
      }
      const now = Date.now();
      const was = status === 'charging';
      const amps = num(dev, 'number', 'charging_amps') || 16;
      const v = num(dev, 'sensor', 'voltage') || 230;
      put(E(dev, 'sensor', 'status'), 'charging', { status_code: '2' });
      put(E(dev, 'sensor', 'power'), Math.round(amps * v));
      put(E(dev, 'sensor', 'measured_current'), amps);
      if (!was) put(E(dev, 'sensor', 'session_energy'), 0, { started_at: iso(now) });
      if (minutes > 0) {
        put(E(dev, 'sensor', 'boost_end'), iso(now + minutes * 60000), { active: true, unlimited: false, minutes });
      } else {
        put(E(dev, 'sensor', 'boost_end'), 'unknown', { active: true, unlimited: true, minutes: 0 });
      }
      put(E(dev, 'switch', 'charging'), 'on');
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
      put(E(dev, 'sensor', 'boost_end'), 'unknown', { active: false, unlimited: false, minutes: 0 });
      put(E(dev, 'switch', 'charging'), 'off');
    }

    function extendCharging(dev, minutes) {
      const b = st(dev, 'sensor', 'boost_end');
      const end = Date.parse(b.state);
      if (b.attributes.active && !b.attributes.unlimited && Number.isFinite(end) && end > Date.now()) {
        put(E(dev, 'sensor', 'boost_end'), iso(end + minutes * 60000), { minutes: (b.attributes.minutes || 0) + minutes });
      } else {
        startCharging(dev, minutes);
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
        config: { time_zone: tz, currency: 'UAH', unit_system: { length: 'km', temperature: '°C' }, version: '2026.10.0', components: [DOMAIN] },
        user: { id: 'preview', name: 'Preview', is_admin: true },
        connection: null,
        callService,
        callWS: async () => ({}),
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
        else if (service === 'extend_charge') extendCharging(dev, Math.max(1, Math.round(data.minutes)));
        else if (service === 'stop_charging') stopCharging(dev);
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
      } else if (domain === 'time' && service === 'set_value') {
        const t = String(data.time || '');
        put(id, t.length === 5 ? t + ':00' : t);
      } else if (domain === 'button' && service === 'press') {
        if (ref.key === 'reset_energy') put(E(dev, 'sensor', 'energy'), 0);
        if (ref.key === 'boost') startCharging(dev, num(dev, 'number', 'boost_minutes') || 30);
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

    /* Simulated backend clock: timed boosts end by themselves, session energy grows. */
    let clock = null;
    let ticks = 0;
    function startClock() {
      if (clock) return;
      clock = setInterval(() => {
        ticks += 1;
        let changed = false;
        for (const id in devs) {
          const dev = devs[id];
          if (st(dev, 'sensor', 'status').state !== 'charging') continue;
          const b = st(dev, 'sensor', 'boost_end');
          const end = Date.parse(b.state);
          if (b.attributes.active && !b.attributes.unlimited && Number.isFinite(end) && end <= Date.now()) {
            stopCharging(dev);
            emitLog({ note: 'mock: boost on ' + id + ' finished, timer restored' });
            changed = true;
          } else if (ticks % 10 === 0) {
            const kw = (num(dev, 'sensor', 'power') || 0) / 1000;
            const add = (kw * 10) / 3600;
            put(E(dev, 'sensor', 'session_energy'), Math.round(((num(dev, 'sensor', 'session_energy') || 0) + add) * 100) / 100);
            put(E(dev, 'sensor', 'energy'), Math.round(((num(dev, 'sensor', 'energy') || 0) + add) * 1000) / 1000);
            changed = true;
          }
        }
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

  g.ElectrosMock = { createMock, installFakeNow, SCENARIOS, KEYS, DOMAIN };
})(typeof globalThis !== 'undefined' ? globalThis : window);
