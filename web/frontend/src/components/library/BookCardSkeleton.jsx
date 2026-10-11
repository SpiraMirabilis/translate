export default function BookCardSkeleton({ t }) {
  return (
    <div className="animate-pulse">
      <div className={`aspect-[2/3] rounded-lg ${t.skeleton}`} />
      <div className={`mt-3 h-3 rounded ${t.skeleton} w-5/6`} />
      <div className={`mt-2 h-2.5 rounded ${t.skeleton} w-1/2`} />
      <div className={`mt-2 h-2.5 rounded ${t.skeleton} w-2/3`} />
    </div>
  )
}
