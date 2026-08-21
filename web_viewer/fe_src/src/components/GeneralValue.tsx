import "./GeneralValue.css";
interface IProps {
  value: number | string;
  unit: string;
  className?: string;
  color?: string;
}

function GeneralValue({ value, unit, className, color }: IProps) {
  return (
    <div className={`${className || ''} general-value`} style={color ? { color } : undefined}>
      <strong>{value}</strong>
      {unit}
    </div>
  );
}
export default GeneralValue;
