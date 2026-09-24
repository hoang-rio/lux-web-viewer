import { useCallback, useEffect, useRef, useState, lazy, useMemo } from "react";
import { useTranslation } from "react-i18next";
import "./App.css";
import {
  IUpdateChart,
  IInverterData,
  IAuthUser,
  IUserInverter,
} from "./Intefaces";
import Footer from "./components/Footer";
import Loading from "./components/Loading";
import * as logUtil from "./utils/logUtil";
import { apiFetch, apiGetJsonOrThrow } from "./utils/fetchUtil";

const SystemInformation = lazy(() => import("./components/SystemInformation"));
const Summary = lazy(() => import("./components/Summary"));
const HourlyChart = lazy(() => import("./components/HourlyChart"));
const EnergyChart = lazy(() => import("./components/EnergyChart"));
const AuthPanel = lazy(() => import("./components/AuthPanel"));
const InverterManageDashboard = lazy(() => import("./components/InverterManageDashboard"));
const TopAuthBar = lazy(() => import("./components/TopAuthBar"));

const MAX_RECONNECT_COUNT = 5;
const RECONNECT_BASE_DELAY_MS = 250;
const RECONNECT_MAX_DELAY_MS = 1000;
const INVERTER_OFFLINE_TIMEOUT_MS = 20 * 60 * 1000;
const ACCESS_TOKEN_KEY = "lux_access_token";
const REFRESH_TOKEN_KEY = "lux_refresh_token";
const INVERTER_ID_KEY = "lux_selected_inverter_id";

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
  const [authUser, setAuthUser] = useState<IAuthUser | null>(null);
  const [authRequired, setAuthRequired] = useState(false);
  const [authConfigLoaded, setAuthConfigLoaded] = useState(false);
  const [isAuthChecking, setIsAuthChecking] = useState(true);
  const [userInverters, setUserInverters] = useState<IUserInverter[]>([]);
  const [selectedInverterId, setSelectedInverterId] = useState<string>("");
  const [showInverterManager, setShowInverterManager] = useState(false);
  const [accessToken, setAccessToken] = useState<string>("");
  const sseAbortControllerRef = useRef<AbortController | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectCountRef = useRef<number>(0);
  const [isSSEConnected, setIsSSEConnected] = useState<boolean>(false);
  const hourlyChartfRef = useRef<IUpdateChart>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [hasStartedRealtimeRequests, setHasStartedRealtimeRequests] = useState(false);
  const [isInitialRealtimeLoading, setIsInitialRealtimeLoading] = useState(false);
  const isFetchingRef = useRef(false);
  const hasInverterDataRef = useRef(false);
  const deviceTimeRef = useRef<string>("");

  // Server-authoritative unread notification count (synced via SSE to all devices)
  const [unreadCountSync, setUnreadCountSync] =
    useState<{ seq: number; count: number } | null>(null);

  const isNoInverterOnboarding = authConfigLoaded && authRequired && !!authUser && userInverters.length === 0;
  const isAuthScreen = authConfigLoaded && authRequired && !authUser;

  const useBearerAuth = Boolean(accessToken);
  const authSessionReady = authConfigLoaded && (!authRequired || !isAuthChecking);
  const hasSSEPrerequisites = !authRequired || (!!authUser && (!useBearerAuth || !!selectedInverterId));
  const isOfflineByDeviceTimeRef = useRef(false);

  const isOfflineByDeviceTime = useMemo(() => {
    const deviceTs = toTimestamp(inverterData?.deviceTime);
    return !(Boolean(deviceTs) && Date.now() - deviceTs <= INVERTER_OFFLINE_TIMEOUT_MS);
  }, [inverterData?.deviceTime, isSSEConnected, toTimestamp]);

  useEffect(() => {
    isOfflineByDeviceTimeRef.current = isOfflineByDeviceTime;
  }, [isOfflineByDeviceTime]);

  const setConnectedTitle = useCallback((deviceTimeOnly?: string) => {
    if (document.hidden || isOfflineByDeviceTimeRef.current) {
      return;
    }
    const currentDeviceTime = deviceTimeOnly || deviceTimeRef.current;
    if (currentDeviceTime) {
      document.title = `[${currentDeviceTime}] ${i18n.t("webTitle")}`;
    }
  }, [i18n]);

  const setOfflineTitle = useCallback(() => {
    if (!isNoInverterOnboarding && !isAuthScreen) {
      document.title = `[${i18n.t("offline")}] ${i18n.t("webTitle")}`;
    }
  }, [i18n, isAuthScreen, isNoInverterOnboarding]);

  const handleSSEPayload = useCallback((rawData: string) => {
    if (document.hidden) {
      return;
    }
    const jsonData = JSON.parse(rawData);
    if (jsonData.event === "update_unread_count") {
      setUnreadCountSync((prev) => ({
        seq: (prev?.seq ?? 0) + 1,
        count: jsonData.data?.unread_count ?? 0,
      }));
    } else {
      setIsInitialRealtimeLoading(false);
      hasInverterDataRef.current = true;
      setInverterData(jsonData.inverter_data);
      if (jsonData.hourly_chart_item) {
        hourlyChartfRef.current?.updateItem(jsonData.hourly_chart_item);
      }
      setIsLoading(false);
    }
  }, [selectedInverterId]);

  useEffect(() => {
    hasInverterDataRef.current = Boolean(inverterData);
  }, [inverterData]);

  const scheduleSSEReconnect = useCallback((reconnect: () => void, immediate = false) => {
    if (document.hidden) {
      return;
    }

    if (immediate) {
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
      reconnectCountRef.current = 0;
      reconnect();
      return;
    }

    if (reconnectCountRef.current >= MAX_RECONNECT_COUNT) {
      logUtil.warn(i18n.t("sse.stopReconnect"), MAX_RECONNECT_COUNT);
      return;
    }

    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
    }
    reconnectCountRef.current++;
    logUtil.log(i18n.t("sse.reconnecting"), reconnectCountRef.current);
    const delayMs = Math.min(
      RECONNECT_BASE_DELAY_MS * reconnectCountRef.current,
      RECONNECT_MAX_DELAY_MS
    );
    reconnectTimeoutRef.current = setTimeout(() => {
      reconnectTimeoutRef.current = null;
      if (!document.hidden) {
        reconnect();
      }
    }, delayMs);
  }, [i18n]);

  const connectSSE = useCallback(() => {
    if (!authSessionReady) {
      return;
    }
    if (authRequired && !authUser) {
      return;
    }
    if (useBearerAuth && !selectedInverterId) {
      return;
    }
    if (document.hidden) {
      return;
    }
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    // Ensure refs are clean before attempting connection
    if (sseAbortControllerRef.current) {
      sseAbortControllerRef.current.abort();
      sseAbortControllerRef.current = null;
    }
    logUtil.log(i18n.t("sse.connecting"));
    const sseParams = new URLSearchParams();
    if (selectedInverterId) {
      sseParams.set("inverter_id", selectedInverterId);
    }
    const ssePath = sseParams.toString() ? `/events?${sseParams.toString()}` : "/events";

    setHasStartedRealtimeRequests(true);
    if (!hasInverterDataRef.current) {
      setIsInitialRealtimeLoading(true);
    }
    const abortController = new AbortController();
    sseAbortControllerRef.current = abortController;

    void (async () => {
      try {
        const response = await apiFetch(ssePath, {
          withAuth: useBearerAuth,
          headers: {
            Accept: "text/event-stream",
          },
          signal: abortController.signal,
        });

        if (!response.ok || !response.body) {
          throw new Error(i18n.t("sse.requestFailedWithStatus", { status: response.status }));
        }

        reconnectCountRef.current = 0;
        logUtil.log(i18n.t("sse.connected"));
        setConnectedTitle();
        setIsSSEConnected(true);

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          // Check if abort signal was triggered
          if (abortController.signal.aborted) {
            break;
          }
          const { value, done } = await reader.read();
          if (done) {
            break;
          }
          buffer += decoder.decode(value, { stream: true });
          // Normalize CRLF to LF to parse SSE frames consistently.
          buffer = buffer.replace(/\r\n/g, "\n");

          let separatorIndex = buffer.indexOf("\n\n");
          while (separatorIndex !== -1) {
            const rawEvent = buffer.slice(0, separatorIndex);
            buffer = buffer.slice(separatorIndex + 2);

            const dataLines = rawEvent
              .split("\n")
              .filter((line) => line.startsWith("data:"))
              .map((line) => line.slice(5).trim());

            if (dataLines.length > 0) {
              handleSSEPayload(dataLines.join("\n"));
            }

            separatorIndex = buffer.indexOf("\n\n");
          }
        }

        sseAbortControllerRef.current = null;
        setIsSSEConnected(false);
        setIsInitialRealtimeLoading(false);
        scheduleSSEReconnect(connectSSE);
      } catch (error) {
        sseAbortControllerRef.current = null;
        if (abortController.signal.aborted) {
          return;
        }
        setIsInitialRealtimeLoading(false);
        setOfflineTitle();
        setIsSSEConnected(false);
        logUtil.error(i18n.t("sse.error"), error);
        scheduleSSEReconnect(connectSSE);
      }
    })();
  }, [authRequired, authSessionReady, authUser, handleSSEPayload, i18n, scheduleSSEReconnect, selectedInverterId, setConnectedTitle, setOfflineTitle, useBearerAuth]);

  const closeSSE = useCallback(() => {
    logUtil.log(i18n.t("sse.closing"));
    setOfflineTitle();
    setIsSSEConnected(false);
    if (sseAbortControllerRef.current) {
      sseAbortControllerRef.current.abort();
      sseAbortControllerRef.current = null;
    }
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    reconnectCountRef.current = 0;
  }, [i18n, setOfflineTitle]);

  const fetchState = useCallback(async () => {
    try {
      if (!authSessionReady) {
        return;
      }
      if (document.hidden) {
        return;
      }
      if (authRequired && !accessToken) {
        return;
      }
      if (useBearerAuth && !selectedInverterId) {
        return;
      }
      if (isFetchingRef.current) {
        return;
      }
      setHasStartedRealtimeRequests(true);
      if (!hasInverterDataRef.current) {
        setIsInitialRealtimeLoading(true);
      }
      isFetchingRef.current = true;
      const params = new URLSearchParams();
      if (selectedInverterId) {
        params.set("inverter_id", selectedInverterId);
      }
      const path = params.toString() ? `/state?${params.toString()}` : "/state";
      const json = await apiGetJsonOrThrow<IInverterData>(path, {
        withAuth: true,
      });
      if (Object.keys(json).length !== 0) {
        hasInverterDataRef.current = true;
        setInverterData(json);
        setIsLoading(false);
      }
    } catch (err) {
      logUtil.error(i18n.t("getStateError"), err);
    } finally {
      // Only clear initial loading if we already have data or no SSE is actively connecting.
      // If SSE is in progress and we have no data yet, let SSE clear the loading state
      // when the first event arrives (or when SSE errors out).
      if (hasInverterDataRef.current || !sseAbortControllerRef.current) {
        setIsInitialRealtimeLoading(false);
      }
      isFetchingRef.current = false;
    }
  }, [accessToken, authRequired, authSessionReady, i18n, selectedInverterId, useBearerAuth]);

  const loadAuthConfig = useCallback(async () => {
    try {
      const res = await apiFetch("/auth/config");
      const json = await res.json();
      setAuthRequired(Boolean(json?.auth_required));
    } catch {
      setAuthRequired(false);
    } finally {
      setAuthConfigLoaded(true);
    }
  }, []);

  const clearAuthSession = useCallback(() => {
    localStorage.removeItem(ACCESS_TOKEN_KEY);
    localStorage.removeItem(REFRESH_TOKEN_KEY);
    setAccessToken("");
    setAuthUser(null);
    setUserInverters([]);
    setSelectedInverterId("");
    setShowInverterManager(false);
    setHasStartedRealtimeRequests(false);
    setIsInitialRealtimeLoading(false);
    hasInverterDataRef.current = false;
  }, []);

  const loadAuthSession = useCallback(async () => {
    if (!authConfigLoaded) {
      return;
    }
    if (!authRequired) {
      setIsAuthChecking(false);
      return;
    }

    setIsAuthChecking(true);

    const token = localStorage.getItem(ACCESS_TOKEN_KEY) || "";
    if (!token) {
      clearAuthSession();
      setIsLoading(false);
      setIsAuthChecking(false);
      return;
    }

    setAccessToken(token);
    try {
      const profileRes = await apiFetch("/auth/profile", {
        withAuth: true,
      });
      const profileJson = await profileRes.json();
      if (!profileRes.ok || !profileJson.success) {
        clearAuthSession();
        setIsLoading(false);
        return;
      }
      setAuthUser(profileJson.user);

      const invRes = await apiFetch("/inverters", {
        withAuth: true,
      });
      const invJson = await invRes.json();
      if (!invRes.ok || !invJson.success || !Array.isArray(invJson.inverters)) {
        setUserInverters([]);
        setSelectedInverterId("");
        setIsLoading(false);
        return;
      }
      setUserInverters(invJson.inverters);
      const savedInverterId = localStorage.getItem(INVERTER_ID_KEY) || "";
      const defaultInverterId =
        invJson.inverters.find((inv: IUserInverter) => inv.id === savedInverterId)?.id ||
        invJson.inverters[0]?.id ||
        "";
      setSelectedInverterId(defaultInverterId);
      if (defaultInverterId) {
        localStorage.setItem(INVERTER_ID_KEY, defaultInverterId);
      }
      setIsLoading(false);
    } catch (err) {
      logUtil.error(i18n.t("auth.loadSessionError"), err);
      clearAuthSession();
      setIsLoading(false);
    } finally {
      setIsAuthChecking(false);
    }
  }, [authConfigLoaded, authRequired, clearAuthSession, i18n]);

  const onAuthSuccess = useCallback(async (token: string, refreshToken: string) => {
    localStorage.setItem(ACCESS_TOKEN_KEY, token);
    localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
    setAccessToken(token);
    await loadAuthSession();
    await fetchState();
  }, [fetchState, loadAuthSession]);

  const onInverterChanged = useCallback(async () => {
    setIsLoading(true);
    await loadAuthSession();
    await fetchState();
    setIsLoading(false);
  }, [fetchState, loadAuthSession]);

  const handleLogout = useCallback(async () => {
    const refreshToken = localStorage.getItem(REFRESH_TOKEN_KEY) || "";
    try {
      await apiFetch("/auth/logout", {
        method: "POST",
        withAuth: true,
        body: JSON.stringify({ refresh_token: refreshToken }),
        headers: {
          "Content-Type": "application/json",
        },
      });
    } catch (err) {
      logUtil.error(i18n.t("auth.logoutRequestFailed"), err);
    } finally {
      clearAuthSession();
      setIsLoading(false);
    }
  }, [clearAuthSession, i18n]);

  useEffect(() => {
    loadAuthConfig();
  }, [loadAuthConfig]);

  useEffect(() => {
    loadAuthSession();
  }, [loadAuthSession]);

  useEffect(() => {
    const onSessionExpired = () => {
      clearAuthSession();
      setIsLoading(false);
    };
    window.addEventListener("lux:session-expired", onSessionExpired);
    return () => {
      window.removeEventListener("lux:session-expired", onSessionExpired);
    };
  }, [clearAuthSession]);

  useEffect(() => {
    if (!authSessionReady || !hasSSEPrerequisites) {
      return;
    }
    if (!document.hidden) {
      connectSSE();
    }
    window.addEventListener("beforeunload", closeSSE);
    return () => {
      window.removeEventListener("beforeunload", closeSSE);
      closeSSE();
    };
  }, [authSessionReady, closeSSE, connectSSE, hasSSEPrerequisites]);

  useEffect(() => {
    if (!authSessionReady || !hasSSEPrerequisites) {
      return;
    }
    fetchState();
  }, [authSessionReady, fetchState, hasSSEPrerequisites]);

  useEffect(() => {
    if (!authSessionReady || !useBearerAuth || !selectedInverterId) {
      return;
    }
    fetchState();
  }, [authSessionReady, fetchState, selectedInverterId, useBearerAuth]);

  useEffect(() => {
    const handleVisibilityChange = () => {
      if (document.hidden) {
        // When the page is hidden, close the SSE connection to reduce activity
        closeSSE();
      } else {
        // When back to foreground, reconnect immediately and fetch snapshot in parallel.
        scheduleSSEReconnect(connectSSE, true);
        fetchState();
      }
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [connectSSE, closeSSE, fetchState, scheduleSSEReconnect]);

  useEffect(() => {
    if (selectedInverterId) {
      localStorage.setItem(INVERTER_ID_KEY, selectedInverterId);
    }
  }, [selectedInverterId]);

  useEffect(() => {
    if (!inverterData?.deviceTime) return;
    const deviceTimeOnly = inverterData.deviceTime.split(" ")[1];
    deviceTimeRef.current = deviceTimeOnly;
    setConnectedTitle(deviceTimeOnly);
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

  if (authConfigLoaded && authRequired && isAuthChecking) {
    return (
      <>
        <div className="d-flex card loading align-center justify-center flex-1">
          <Loading />
        </div>
        <Footer />
      </>
    );
  }

  if (authConfigLoaded && authRequired && !authUser) {
    return (
      <>
        <AuthPanel onAuthSuccess={onAuthSuccess} />
        <Footer />
      </>
    );
  }

  if (authConfigLoaded && authRequired && authUser && userInverters.length === 0) {
    return (
      <>
        <TopAuthBar authUser={authUser} onLogout={handleLogout} />
        <div className="flex-1">
          <InverterManageDashboard
            inverters={userInverters}
            selectedInverterId={selectedInverterId}
            onSelectInverter={setSelectedInverterId}
            onChanged={onInverterChanged}
            allowClose={false}
          />
        </div>
        <Footer />
      </>
    );
  }

  if (inverterData) {
    return (
      <>
        {authUser && (
          <TopAuthBar
            authUser={authUser}
            onLogout={handleLogout}
            onManageInverters={() => setShowInverterManager(true)}
            inverters={userInverters}
            selectedInverterId={selectedInverterId}
            onSelectInverter={setSelectedInverterId}
          />
        )}
        {authUser && showInverterManager && (
          <InverterManageDashboard
            inverters={userInverters}
            selectedInverterId={selectedInverterId}
            onSelectInverter={setSelectedInverterId}
            onChanged={onInverterChanged}
            onClose={() => setShowInverterManager(false)}
          />
        )}
        <Summary
          invertData={inverterData}
          selectedInverterId={selectedInverterId}
          authToken={accessToken}
        />
        <SystemInformation
          inverterData={inverterData}
          isSSEConnected={isSSEConnected}
          unreadCountSync={unreadCountSync}
          inverters={userInverters}
          isOffline={isOfflineByDeviceTime}
          selectedInverterId={selectedInverterId}
          onReconnect={connectSSE}
        />
        <div className="row chart">
          <HourlyChart
            ref={hourlyChartfRef}
            className="flex-1 chart-item"
            selectedInverterId={selectedInverterId}
            authToken={accessToken}
          />
          <EnergyChart
            className="flex-1 chart-item"
            selectedInverterId={selectedInverterId}
            authToken={accessToken}
          />
        </div>
        <Footer />
      </>
    );
  }

  if (authSessionReady && hasSSEPrerequisites && !inverterData && (!hasStartedRealtimeRequests || isInitialRealtimeLoading)) {
    return (
      <>
        {authUser && (
          <TopAuthBar
            authUser={authUser}
            onLogout={handleLogout}
            onManageInverters={() => setShowInverterManager(true)}
            inverters={userInverters}
            selectedInverterId={selectedInverterId}
            onSelectInverter={setSelectedInverterId}
          />
        )}
        {authUser && showInverterManager && (
          <InverterManageDashboard
            inverters={userInverters}
            selectedInverterId={selectedInverterId}
            onSelectInverter={setSelectedInverterId}
            onChanged={onInverterChanged}
            onClose={() => setShowInverterManager(false)}
          />
        )}
        <div className="d-flex card loading align-center justify-center flex-1">
          <Loading />
        </div>
        <Footer />
      </>
    );
  }

  return (
    <>
      {authUser && (
        <TopAuthBar
          authUser={authUser}
          onLogout={handleLogout}
          onManageInverters={() => setShowInverterManager(true)}
          inverters={userInverters}
          selectedInverterId={selectedInverterId}
          onSelectInverter={setSelectedInverterId}
        />
      )}
      {authUser && showInverterManager && (
        <InverterManageDashboard
          inverters={userInverters}
          selectedInverterId={selectedInverterId}
          onSelectInverter={setSelectedInverterId}
          onChanged={onInverterChanged}
          onClose={() => setShowInverterManager(false)}
        />
      )}
      <div className="d-flex card server-offline align-center justify-center flex-1">
        {t("server.offline")}
      </div>
      <Footer />
    </>
  );
}

export default App;
