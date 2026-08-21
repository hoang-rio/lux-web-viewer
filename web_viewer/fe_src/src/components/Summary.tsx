import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { IInverterData, ITotal } from "../Intefaces";
import GeneralValue from "./GeneralValue";
import "./Summary.css";
import DisplayYield from "./DisplayYield";
import YieldChart from "./YieldChart";
import { formatTotalValue } from "./utils";
import { useTranslation } from 'react-i18next';
import * as logUtil from "../utils/logUtil";
import { apiGetJsonOrThrow } from "../utils/fetchUtil";

interface IProps {
  invertData: IInverterData;
  selectedInverterId?: string;
  authToken?: string;
}
enum YieldDisplay {
  YIELD,
  CHART_TODAY,
  CHART_TOTAL,
}
function Summary({ invertData, selectedInverterId, authToken }: IProps) {
  const { i18n, t } = useTranslation();
  const [isShowCharged, setIsShowCharged] = useState(false);
  const [isShowFeed, setIsShowFeed] = useState(false);
  const isFetchingRef = useRef(false);
  const [total, setTotal] = useState<ITotal>();
  const [yieldDisplay, setYieldDisplay] = useState<YieldDisplay>(
    YieldDisplay.YIELD
  );

  // Zero out "today" energy fields if the inverter's deviceTime is not today's date.
  const todayInvertData = useMemo(() => {
    if (!invertData.deviceTime) return invertData;
    const deviceDate = invertData.deviceTime.split(" ")[0]; // "YYYY-MM-DD"
    const todayDate = new Date().toLocaleDateString("sv"); // "YYYY-MM-DD" in local time
    if (deviceDate !== todayDate) {
      return {
        ...invertData,
        e_pv_day: 0,
        e_pv_1_day: 0,
        e_pv_2_day: 0,
        e_pv_3_day: 0,
        e_inv_day: 0,
        e_rec_day: 0,
        e_chg_day: 0,
        e_dischg_day: 0,
        e_eps_day: 0,
        e_to_grid_day: 0,
        e_to_user_day: 0,
      };
    }
    return invertData;
  }, [invertData]);

  const switchYieldDisplay = useCallback(() => {
    switch (yieldDisplay) {
      case YieldDisplay.YIELD:
        setYieldDisplay(YieldDisplay.CHART_TODAY);
        break;
      case YieldDisplay.CHART_TODAY:
        setYieldDisplay(YieldDisplay.CHART_TOTAL);
        break;
      default:
        setYieldDisplay(YieldDisplay.YIELD);
        break;
    }
  }, [yieldDisplay]);

  const fetchTotal = useCallback(async () => {
    if (isFetchingRef.current) {
      return;
    }
    try {
      logUtil.log(i18n.t('fetchingTotal'));
      isFetchingRef.current = true;
      const params = new URLSearchParams();
      if (selectedInverterId) {
        params.set("inverter_id", selectedInverterId);
      }
      const path = params.toString() ? `/total?${params.toString()}` : "/total";
      const json = await apiGetJsonOrThrow<ITotal>(path, { withAuth: Boolean(authToken) });
      setTotal(json);
    } catch (err) {
      logUtil.error(i18n.t('fetchTotalError'), err);
    } finally {
      isFetchingRef.current = false;
    }
  }, [authToken, i18n, selectedInverterId]);

  const onVisibilityChange = useCallback(() => {
    if (!document.hidden) {
      fetchTotal();
    }
  }, [fetchTotal]);

  useEffect(() => {
    fetchTotal();
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [fetchTotal, onVisibilityChange]);

  // Extract yield content rendering
  const renderYieldContent = useCallback(() => {
    switch (yieldDisplay) {
      case YieldDisplay.YIELD:
        return <DisplayYield total={total} ePVDay={todayInvertData.e_pv_day} />;
      case YieldDisplay.CHART_TODAY:
        return (
          <YieldChart
            label="today"
            totalYield={todayInvertData.e_pv_day}
            charge={todayInvertData.e_chg_day - todayInvertData.e_rec_day}
            gridExport={todayInvertData.e_to_grid_day}
          />
        );
      case YieldDisplay.CHART_TOTAL:
        return (
          <YieldChart
            label="total"
            totalYield={total?.pv || 0}
            charge={total?.battery_charged || 0}
            gridExport={total?.grid_export || 0}
          />
        );
      default:
        return null;
    }
  }, [yieldDisplay, total, todayInvertData]);

  return (
    <div className="summary row">
      <div
        className="yield summary-item flex-1 col"
        onClick={switchYieldDisplay}
      >
        <div className="summary-item-title">{t('solarYield')}</div>
        <div className="summary-item-content col flex-1">
          {renderYieldContent()}
        </div>
      </div>
      <div
        className="battery summary-item flex-1"
        onClick={() => setIsShowCharged(!isShowCharged)}
      >
        <div className="summary-item-title">
          {isShowCharged ? t('batteryCharged') : t('batteryDischarge')}
        </div>
        <div className="summary-item-content">
          <div className="row justify-space-between align-center">
            <img src="/assets/icon_battery_discharging.png" />
            <div className="summary-item-content-texts">
              <GeneralValue
                value={
                  isShowCharged ? todayInvertData.e_chg_day : todayInvertData.e_dischg_day
                }
                unit=" kWh"
              />
              <div className="description">
                {t('today')}
              </div>
              {total && (
                <>
                  <GeneralValue
                    {...formatTotalValue(
                      isShowCharged ? total.battery_charged : total.battery_discharged
                    )}
                  />
                  <div className="description">
                    {t('total')}
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      </div>
      <div
        className="feed summary-item flex-1"
        onClick={() => setIsShowFeed(!isShowFeed)}
      >
        <div className="summary-item-title ">
          {isShowFeed ? t('feedInEnergy') : t('import')}
        </div>
        <div className="summary-item-content">
          <div className="row justify-space-between align-center">
            <img
              src={
                isShowFeed
                  ? "/assets/icon_feed_in_energy.png"
                  : "/assets/icon_import.png"
              }
            />
            <div className="feed-texts summary-item-content-texts">
              <div className="col">
                <GeneralValue
                  value={
                    isShowFeed ? todayInvertData.e_to_grid_day : todayInvertData.e_to_user_day
                  }
                  unit=" kWh"
                />
                <div className="description">
                  {t('today')}
                </div>
                {total && (
                  <>
                    <GeneralValue
                      {...formatTotalValue(
                        isShowFeed ? total.grid_export : total.grid_import
                      )}
                    />
                    <div className="description">
                      {t('total')}
                    </div>
                  </>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
      <div className="comsumption summary-item flex-1">
        <div className="summary-item-title ">{t('consumption')}</div>
        <div className="summary-item-content">
          <div className="row justify-space-between align-center">
            <img src="/assets/icon_consumption.png" />
            <div className="feed-texts summary-item-content-texts">
              <GeneralValue
                value={(
                  todayInvertData.e_inv_day +
                  todayInvertData.e_to_user_day +
                  todayInvertData.e_eps_day -
                  todayInvertData.e_rec_day
                ).toFixed(1)}
                unit=" kWh"
              />
              <div className="description">{t('today')}</div>
              {total && (
                <>
                  <GeneralValue {...formatTotalValue(total.consumption)} />
                  <div className="description">{t('total')}</div>
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
export default memo(Summary);
