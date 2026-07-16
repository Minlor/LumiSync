import * as lucideIcons from "lucide-react";
import { FaGithub, FaLinux, FaPython, FaWindows } from "react-icons/fa";

const lucideSource = (lucideIcons as typeof lucideIcons & { default?: typeof lucideIcons }).default ?? lucideIcons;
const { ArrowDownToLine, ArrowRight, Monitor, Music2, PackageOpen, SlidersHorizontal } = lucideSource;

const downloads = {
  windows: "https://github.com/Minlor/LumiSync/releases/latest/download/LumiSync-Windows-x64-onefile.exe",
  portable: "https://github.com/Minlor/LumiSync/releases/latest/download/LumiSync-Windows-x64-portable.zip",
  linux: "https://github.com/Minlor/LumiSync/releases/latest/download/LumiSync-x86_64.AppImage",
  pypi: "https://pypi.org/project/lumisync/",
};

const features = [
  { title: "Match your screen", copy: "Mirror the colours at the edge of your display across a single light or an entire room.", icon: Monitor },
  { title: "React to music", copy: "Turn audio into responsive colour and motion, with Auto Director when you want it hands-free.", icon: Music2 },
  { title: "Control everything", copy: "Discover, group, dim, recolour, and power supported lights from one focused desktop app.", icon: SlidersHorizontal },
];

const devices = [
  ["Govee", "LAN / UDP", "Strips and bulbs"],
  ["iDotMatrix", "Bluetooth LE", "Pixel displays"],
  ["LSC / Tuya", "Local network", "Wi-Fi lights"],
];

export default function Home() {
  return (
    <main>
      <nav className="nav" aria-label="Main navigation">
        <a className="brand" href="#top" aria-label="LumiSync home">
          <img src="/lumisync-mark.png" alt="" />
          <span>LumiSync</span>
        </a>
        <div className="navLinks">
          <a href="#features">Features</a>
          <a href="#devices">Devices</a>
          <a href="https://github.com/Minlor/LumiSync" target="_blank" rel="noreferrer"><FaGithub aria-hidden="true" /> GitHub</a>
          <a className="navDownload" href="#download">Download</a>
        </div>
      </nav>

      <section className="hero" id="top">
        <div className="heroCopy">
          <p className="eyebrow">Free &amp; open source · Windows &amp; Linux</p>
          <h1>Light that follows<br />what you love.</h1>
          <p className="heroLead">LumiSync makes your Govee, iDotMatrix, and Tuya lights react to your screen and music. It runs locally, stays private, and keeps setup simple.</p>
          <div className="heroActions">
            <a className="button primary" href={downloads.windows}><FaWindows aria-hidden="true" /> Download for Windows <ArrowDownToLine aria-hidden="true" /></a>
            <a className="button secondary" href={downloads.linux}><FaLinux aria-hidden="true" /> Download for Linux</a>
          </div>
          <p className="heroNote">No account · No cloud required · Just your lights.</p>
        </div>
        <div className="heroPreview">
          <img src="/images/music-sync.png" alt="LumiSync desktop app showing music sync controls" />
        </div>
      </section>

      <section className="featureSection" id="features">
        <div className="sectionHeading">
          <p className="eyebrow">Made for the room you are in</p>
          <h2>One simple app.<br />Three useful modes.</h2>
        </div>
        <div className="featureGrid">
          {features.map(({ title, copy, icon: Icon }) => (
            <article className="featureCard" key={title}>
              <span className="featureIcon"><Icon size={25} aria-hidden="true" /></span>
              <h3>{title}</h3>
              <p>{copy}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="productSection">
        <div className="productCopy">
          <p className="eyebrow">Monitor sync</p>
          <h2>Bring the screen<br />into the room.</h2>
          <p>Choose a display, map its regions to your LEDs, then adjust brightness, smoothing, saturation, and frame rate until it feels right.</p>
          <ul>
            <li>Multi-monitor selection</li>
            <li>Custom LED region mapping</li>
            <li>Groups and individual lights</li>
          </ul>
        </div>
        <img className="productScreenshot" src="/images/monitor-sync.png" alt="LumiSync monitor sync settings" />
      </section>

      <section className="productSection productReverse">
        <div className="productCopy">
          <p className="eyebrow">Music sync</p>
          <h2>Let every beat<br />set the mood.</h2>
          <p>Choose a reaction and a palette yourself, or let Auto Director follow the energy so the lighting stays alive without needing attention.</p>
          <ul>
            <li>Audio-reactive patterns</li>
            <li>Curated colour palettes</li>
            <li>Automatic scene direction</li>
          </ul>
        </div>
        <img className="productScreenshot" src="/images/music-sync.png" alt="LumiSync music sync settings" />
      </section>

      <section className="deviceSection" id="devices">
        <div className="sectionHeading compactHeading">
          <p className="eyebrow">Supported devices</p>
          <h2>Different brands.<br />One control room.</h2>
          <p>LumiSync connects directly over your local network or Bluetooth, so your lighting data stays at home.</p>
        </div>
        <div className="deviceContent">
          <img src="/images/devices.png" alt="LumiSync device management screen" />
          <div className="deviceTable" role="table" aria-label="Supported device families">
            <div className="tableHead" role="row"><span>Family</span><span>Connection</span><span>Products</span></div>
            {devices.map(([family, connection, products]) => (
              <div className="tableRow" role="row" key={family}><strong>{family}</strong><span>{connection}</span><span>{products}</span></div>
            ))}
          </div>
        </div>
      </section>

      <section className="download" id="download">
        <img src="/lumisync-app.png" alt="LumiSync app icon" />
        <p className="eyebrow">Ready to try it?</p>
        <h2>Make room react.</h2>
        <p>Download LumiSync, connect a supported light, and start syncing in minutes.</p>
        <div className="downloadGrid">
          <a className="downloadOption featured" href={downloads.windows}><FaWindows aria-hidden="true" /><span><strong>Windows</strong><small>Single-file installer · x64</small></span><ArrowDownToLine aria-hidden="true" /></a>
          <a className="downloadOption" href={downloads.portable}><PackageOpen aria-hidden="true" /><span><strong>Windows portable</strong><small>Extract and run · x64</small></span><ArrowDownToLine aria-hidden="true" /></a>
          <a className="downloadOption" href={downloads.linux}><FaLinux aria-hidden="true" /><span><strong>Linux</strong><small>AppImage · x86_64</small></span><ArrowDownToLine aria-hidden="true" /></a>
          <a className="downloadOption" href={downloads.pypi}><FaPython aria-hidden="true" /><span><strong>Install with pip</strong><small>Python 3.11+ · advanced</small></span><ArrowRight aria-hidden="true" /></a>
        </div>
        <p className="requirements">Windows 10/11 is fully supported. Linux screen sync requires X11; macOS and Wayland capture are in progress.</p>
      </section>

      <footer>
        <a className="brand" href="#top"><img src="/lumisync-mark.png" alt="" /><span>LumiSync</span></a>
        <p>A Minlor project · Screen, sound, and light in sync.</p>
        <div><a href="https://github.com/Minlor/LumiSync">GitHub</a><a href="https://pypi.org/project/lumisync/">PyPI</a><a href="https://ko-fi.com/Minlor">Support</a><a href="https://minlor.net">minlor.net</a></div>
      </footer>
    </main>
  );
}
