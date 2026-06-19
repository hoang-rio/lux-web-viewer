import { useCallback, useEffect, useRef, useState, useMemo, lazy } from "react";
import { useTranslation } from "react-i18next";
import "./App.css";
import { IUpdateChart, IInverterData, INotificationData } from "./Intefaces";
import Footer from "./components/Footer";
import Loading from "./components/Loading";
import * as logUtil from "./utils/logUtil";

const SystemInformation = lazy(() => import("./components/SystemInformation"));
const Summary = lazy(() => import("./components/Summary"));
const HourlyChart = lazy(() => import("./components/HourlyChart"));
const EnergyChart = lazy(() => import("./components/EnergyChart"));

const MAX_RECONNECT_COUNT = 5;
const INVERTER_OFFLINE_TIMEOUT_MS = 20 * 60 * 1000;

function toTimestamp(value?: string | null): number {
  if (!value) {
    return 0;
  }

  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function App() {
  const { t, i18n } = useTranslation();
  const [inverterData, setInverterData] = useState<IInverterData>();
  const eventSourceRef = useRef<EventSource>(undefined);
  const reconnectCountRef = useRef<number>(0);
  const [isSSEConnected, setIsSSEConnected] = useState<boolean>(false);
  const hourlyChartfRef = useRef<IUpdateChart>(null);
  const [isLoading, setIsLoading] = useState(true);
  const isFetchingRef = useRef(false);
  const deviceTimeRef = useRef<string>("");
  const isOfflineByDeviceTimeRef = useRef(false);

  // Changed to hold notification object or null
  const [newNotification, setNewNotification] =
    useState<INotificationData | null>(null);

  const isOfflineByDeviceTime = useMemo(() => {
    const deviceTs = toTimestamp(inverterData?.deviceTime);
    return !(Boolean(deviceTs) && Date.now() - deviceTs <= INVERTER_OFFLINE_TIMEOUT_MS);
  }, [inverterData?.deviceTime, isSSEConnected, toTimestamp]);

  useEffect(() => {
    isOfflineByDeviceTimeRef.current = isOfflineByDeviceTime;
  }, [isOfflineByDeviceTime]);

  const setConnectedTitle = useCallback(() => {
    if (document.hidden || isOfflineByDeviceTimeRef.current) {
      return;
    }
    const currentDeviceTime = deviceTimeRef.current;
    if (currentDeviceTime) {
      document.title = `[${currentDeviceTime}] ${i18n.t("webTitle")}`;
    }
  }, [i18n]);

  const setOfflineTitle = useCallback(() => {
    document.title = `[${i18n.t("offline")}] ${i18n.t("webTitle")}`;
  }, [i18n]);

  const connectSSE = useCallback(() => {
    if (eventSourceRef.current) {
      return;
    }
    logUtil.log(i18n.t("sse.connecting"));
    const eventSource = new EventSource(`${import.meta.env.VITE_API_BASE_URL}/events`);
    eventSourceRef.current = eventSource;

    eventSource.onopen = () => {
      reconnectCountRef.current = 0;
      logUtil.log(i18n.t("sse.connected"));
      setConnectedTitle();
      setIsSSEConnected(true);
    };

    eventSource.onmessage = (event) => {
      const jsonData = JSON.parse(event.data);
      if (jsonData.event === "new_notification") {
        setNewNotification(jsonData.data);
      } else {
        setInverterData(jsonData.inverter_data);
        hourlyChartfRef.current?.updateItem(jsonData.hourly_chart_item);
        setIsLoading(false);
      }
    };

    eventSource.onerror = (event) => {
      setOfflineTitle();
      setIsSSEConnected(false);
      logUtil.error(i18n.t("sse.error"), event);
      eventSource.close();
      eventSourceRef.current = undefined;
      if (reconnectCountRef.current >= MAX_RECONNECT_COUNT) {
        logUtil.warn(i18n.t("sse.stopReconnect"), MAX_RECONNECT_COUNT);
        return;
      }

      reconnectCountRef.current++;
      logUtil.log(i18n.t("sse.reconnecting"), reconnectCountRef.current);
      // eslint-disable-next-line react-hooks/immutability
      setTimeout(() => connectSSE(), 1000 * reconnectCountRef.current);
    };
  }, [i18n]);

  const closeSSE = useCallback(() => {
    logUtil.log(i18n.t("sse.closing"));
    setOfflineTitle();
    setIsSSEConnected(false);
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = undefined;
    }
  }, [i18n]);

  const fetchState = useCallback(async () => {
    try {
      if (isFetchingRef.current) {
        return;
      }
      isFetchingRef.current = true;
      const res = await fetch(`${import.meta.env.VITE_API_BASE_URL}/state`);
      const json = await res.json();
      if (Object.keys(json).length !== 0) {
        setInverterData(json);
        setIsLoading(false);
      }
    } catch (err) {
      logUtil.error(i18n.t("getStateError"), err);
    }
    isFetchingRef.current = false;
  }, [i18n]);

  useEffect(() => {
    if (!eventSourceRef.current && !document.hidden) {
      connectSSE();
    }
    window.addEventListener("beforeunload", closeSSE);
    return () => {
      window.removeEventListener("beforeunload", closeSSE);
      closeSSE();
    };
  }, [connectSSE, closeSSE]);

  useEffect(() => {
    fetchState();
  }, [fetchState]);

  useEffect(() => {
    const handleVisibilityChange = () => {
      if (document.hidden) {
        // When the page is hidden, close the SSE connection to reduce activity
        closeSSE();
      } else {
        // When back to foreground, fetch state and reconnect
        fetchState();
        connectSSE();
      }
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [connectSSE, closeSSE, fetchState]);

  useEffect(() => {
    if (!inverterData?.deviceTime) return;
    const deviceTimeOnly = inverterData.deviceTime.split(" ")[1];
    deviceTimeRef.current = deviceTimeOnly;
    setConnectedTitle();
  }, [inverterData?.deviceTime, setConnectedTitle, t]);

  if (isLoading) {
    return (
      <>
        <div className="d-flex card loading align-center justify-center flex-1">
          <Loading />
        </div>
        <Footer />
      </>
    );
  }

  if (inverterData) {
    return (
      <>
        <Summary invertData={inverterData} />
        <SystemInformation
          inverterData={inverterData}
          isSSEConnected={isSSEConnected}
          isOffline={isOfflineByDeviceTime}
          onReconnect={connectSSE}
          newNotification={newNotification}
        />
        <div className="row chart">
          <HourlyChart ref={hourlyChartfRef} className="flex-1 chart-item" />
          <EnergyChart className="flex-1 chart-item" />
        </div>
        <Footer />
      </>
    );
  }

  return (
    <>
      <div className="d-flex card server-offline align-center justify-center flex-1">
        {t("server.offline")}
      </div>
      <Footer />
    </>
  );
}

export default App;
