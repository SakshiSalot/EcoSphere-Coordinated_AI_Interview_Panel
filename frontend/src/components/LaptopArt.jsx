/* The hero illustration — an isometric workstation.
 *
 * Drawn rather than downloaded: an SVG inherits the palette, scales without a
 * raster asset and costs no request. The screen shows a transcript being
 * marked, which is what the product actually does, and the floating pieces
 * carry the coral accent out into the composition.
 *
 * Contents are drawn in flat local coordinates and projected with a matrix
 * transform. Hand-computing every isometric point is where these get wrong and
 * unfixable.
 */

// local x runs along the screen's width, local y runs up the screen
const SCREEN = "translate(118,316) matrix(0.890,-0.456,0,-1,0,0)";
// local x along the deck's width, local y into the depth
const DECK = "translate(118,316) matrix(0.890,-0.456,0.890,0.456,0,0)";

export default function LaptopArt() {
  return (
    <svg viewBox="0 0 560 440" role="img"
         aria-label="An isometric workstation showing an interview transcript being scored">
      <defs>
        <linearGradient id="screen" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#1a1d47" />
          <stop offset="100%" stopColor="#12142f" />
        </linearGradient>
        <linearGradient id="lid" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#2b2f63" />
          <stop offset="100%" stopColor="#1d2049" />
        </linearGradient>
        <linearGradient id="deck" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#d7dbf0" />
          <stop offset="100%" stopColor="#a9b0d4" />
        </linearGradient>
        <linearGradient id="hot" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#f2542d" />
          <stop offset="100%" stopColor="#c9391a" />
        </linearGradient>
        <radialGradient id="halo" cx="50%" cy="50%">
          <stop offset="0%" stopColor="#f2542d" stopOpacity=".28" />
          <stop offset="100%" stopColor="#f2542d" stopOpacity="0" />
        </radialGradient>
      </defs>

      <ellipse cx="300" cy="230" rx="240" ry="170" fill="url(#halo)" />

      {/* the curve behind, echoing a rising score */}
      <path d="M40 300 C140 300 170 150 250 128 C330 106 360 210 430 168 C470 145 500 96 540 78"
            fill="none" stroke="#f2542d" strokeWidth="1.6" opacity=".5" />
      <path d="M40 300 C140 300 170 150 250 128 C330 106 360 210 430 168 C470 145 500 96 540 78 L540 340 L40 340 Z"
            fill="#f2542d" opacity=".05" />

      {/* wireframe floor */}
      <g stroke="#2b2f63" strokeWidth="1" fill="none" opacity=".85">
        <path d="M56 330 L300 205 L544 330 L300 455 Z" />
        <path d="M178 268 L422 393 M422 268 L178 393" strokeDasharray="4 7" />
      </g>

      {/* --- the laptop --- */}
      <g transform={DECK}>
        <rect x="0" y="0" width="191" height="191" fill="url(#deck)" />
        {/* keys, as bands */}
        <g fill="#8c94bd" opacity=".85">
          {[24, 44, 64, 84].map((y) => (
            <rect key={y} x="18" y={y} width="155" height="11" rx="3" />
          ))}
          <rect x="52" y="112" width="88" height="20" rx="5" />
        </g>
      </g>

      <g transform={SCREEN}>
        <rect x="0" y="0" width="191" height="150" rx="4" fill="url(#lid)" />
        <rect x="7" y="8" width="177" height="134" rx="2.5" fill="url(#screen)" />

        {/* a transcript being marked */}
        <g>
          <rect x="16" y="118" width="46" height="5" rx="2.5" fill="#f2542d" opacity=".9" />
          <rect x="16" y="106" width="120" height="5" rx="2.5" fill="#5560a8" />
          <rect x="16" y="96" width="98" height="5" rx="2.5" fill="#414b8d" />

          <rect x="16" y="76" width="36" height="5" rx="2.5" fill="#f2542d" opacity=".55" />
          <rect x="16" y="64" width="132" height="5" rx="2.5" fill="#5560a8" />
          <rect x="16" y="54" width="112" height="5" rx="2.5" fill="#414b8d" />
          <rect x="16" y="44" width="74" height="5" rx="2.5" fill="#333b76" />

          {/* the score bars */}
          <g>
            <rect x="16" y="24" width="110" height="6" rx="3" fill="#252c60" />
            <rect x="16" y="24" width="84" height="6" rx="3" fill="#f2542d" />
            <rect x="16" y="12" width="110" height="6" rx="3" fill="#252c60" />
            <rect x="16" y="12" width="46" height="6" rx="3" fill="#f2542d" opacity=".7" />
          </g>

          {/* a level meter, top right: somebody is speaking */}
          <g fill="#f2542d">
            <rect x="140" y="116" width="4" height="9" rx="2" opacity=".5" />
            <rect x="148" y="112" width="4" height="13" rx="2" />
            <rect x="156" y="118" width="4" height="7" rx="2" opacity=".75" />
            <rect x="164" y="110" width="4" height="15" rx="2" />
          </g>
        </g>
      </g>

      {/* --- floating pieces --- */}
      {/* a cube, upper left */}
      <g transform="translate(64,150)">
        <path d="M0 18 L32 0 L64 18 L32 36 Z" fill="#e7eaf7" />
        <path d="M0 18 L32 36 L32 62 L0 44 Z" fill="#b9c0e0" />
        <path d="M64 18 L32 36 L32 62 L64 44 Z" fill="#9aa3cd" />
      </g>

      {/* a coral cylinder, lower left */}
      <g transform="translate(78,332)">
        <ellipse cx="26" cy="12" rx="26" ry="12" fill="url(#hot)" />
        <path d="M0 12 L0 28 A26 12 0 0 0 52 28 L52 12 A26 12 0 0 1 0 12 Z" fill="#b53516" />
      </g>

      {/* a small cube, right */}
      <g transform="translate(452,262)">
        <path d="M0 13 L23 0 L46 13 L23 26 Z" fill="#f4d7cd" />
        <path d="M0 13 L23 26 L23 45 L0 32 Z" fill="#e0a893" />
        <path d="M46 13 L23 26 L23 45 L46 32 Z" fill="#c98066" />
      </g>

      {/* a slab, upper right */}
      <g transform="translate(430,120)">
        <path d="M0 16 L30 0 L60 16 L30 32 Z" fill="#dfe3f4" />
        <path d="M0 16 L30 32 L30 44 L0 28 Z" fill="#aeb6d9" />
        <path d="M60 16 L30 32 L30 44 L60 28 Z" fill="#8f99c4" />
      </g>

      {/* thin connectors and nodes, the technical texture */}
      <g stroke="#f2542d" strokeWidth="1" fill="none" opacity=".45">
        <path d="M128 186 L128 258" strokeDasharray="3 6" />
        <path d="M470 300 L470 340 L392 384" strokeDasharray="3 6" />
        <path d="M460 152 L520 152" strokeDasharray="3 6" />
      </g>
      <g fill="#f2542d" opacity=".7">
        <circle cx="520" cy="152" r="3" />
        <circle cx="392" cy="384" r="2.5" />
        <circle cx="40" cy="228" r="2.5" />
        <circle cx="300" cy="60" r="2" />
      </g>
    </svg>
  );
}
