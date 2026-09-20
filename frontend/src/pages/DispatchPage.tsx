import { useEffect, useState } from "react";
import { api } from "../api/client";
type Call = { id: number; floor: number; direction: string; passengers: number; status: string; score: string; assigned_car_id: number | null };
export default function DispatchPage() {
  const [rows, setRows] = useState<Call[]>([]);
  const [msg, setMsg] = useState(""); const [err, setErr] = useState("");
  const reload = () => api<Call[]>("/calls").then(setRows);
  useEffect(() => { reload(); }, []);
  async function run(id: number) {
    setMsg(""); setErr("");
    try {
      const c = await api<Call>("/dispatch", { method: "POST", body: JSON.stringify({ call_id: id }) });
      setMsg(`呼梯 #${c.id} → 轿厢 ${c.assigned_car_id}，评分 ${c.score}`);
      reload();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); reload(); }
  }
  const waiting = rows.filter(r => r.status === "waiting");
  const rejected = rows.filter(r => r.status === "rejected");
  return (<>
    <h2>派工</h2>
    {msg && <div className="ok">{msg}</div>}
    {err && <div className="err">{err}</div>}
    {rejected.length > 0 && (
      <div className="err">
        {rejected.length} 单呼梯（{rejected.map(c => `#${c.id}`).join("、")}）曾因轿厢满员被拒绝，
        即使轿厢腾出容量也不会再派，请在呼梯页重新登记。
      </div>
    )}
    <table className="table"><thead><tr><th>呼梯</th><th>楼层</th><th>方向</th><th>人数</th><th></th></tr></thead>
    <tbody>{waiting.map(c => <tr key={c.id}><td>#{c.id}</td><td>{c.floor}</td><td>{c.direction}</td><td>{c.passengers}</td>
      <td><button onClick={() => run(c.id)}>评分派轿厢</button></td></tr>)}
      {!waiting.length && <tr><td colSpan={5}>暂无待派呼梯</td></tr>}
    </tbody></table>
  </>);
}
