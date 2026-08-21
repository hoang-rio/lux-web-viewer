import "./GeneralValue.css";
interface IProps {
  value: number | string;
  unit: string;
  className?: string;
  unitClassName?: string;
}

function GeneralValue({ value, unit, className, unitClassName }: IProps) {
  return (
    <div className={`${className || ''} general-value`}>
      <strong>{value}</strong>
      <span className={unitClassName}>{unit}</span>
    </div>
  );
}
export default GeneralValue;
