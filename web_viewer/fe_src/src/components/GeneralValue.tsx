import "./GeneralValue.css";
interface IProps {
  value: number | string;
  unit: string;
  className?: string;
  color?: string;
}

function GeneralValue({ value, unit, className, color }: IProps) {
  return (
    <div className={`${className || ''} general-value`}>
      <strong>{value}</strong>
      <span style={color ? { color } : undefined}>{unit}</span>
    </div>
  );
}
export default GeneralValue;
