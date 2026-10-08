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

const accountGuide = "https://github.com/Minlor/LumiSync/blob/main/docs/vendor-accounts.md";
const releaseNotes = "https://github.com/Minlor/LumiSync/releases/tag/0.8.1";

function Screenshot({ name, alt, caption }: { name: string; alt: string; caption: string }) {
  return <figure className="screenshot">
    <a href={`/images/${name}.png`} aria-label={`Open full-size screenshot: ${alt}`}>
      <img src={`/images/${name}.png`} alt={alt} loading="lazy" />
    </a>
    <figcaption>{caption} Example data.</figcaption>
  </figure>;
}

const features = [
  { title: "Match your screen", copy: "Mirror the colours at the edge of your display across a single light or an entire room.", icon: Monitor },
  { title: "React to music", copy: "Turn audio into responsive colour and motion, with Auto Director when you want it hands-free.", icon: Music2 },
  { title: "Control lights and plugs", copy: "Use the controls each device supports. Switches get power controls; compatible plugs also show electrical readings.", icon: SlidersHorizontal },
];

const devices = [
  ["Govee", "LAN / account / API", "Strips and bulbs"],
  ["iDotMatrix", "Bluetooth LE", "Pixel displays"],
  ["LSC / Tuya", "LAN / account", "Lights, switches and plugs"],
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
          <h1>Lights and plugs.<br />One desktop app.</h1>
          <p className="heroLead">Control Govee, Tuya and LSC devices from your desktop. Sync local lights with your screen or music, draw on iDotMatrix panels, and check supported plugs&apos; energy use.</p>
          <div className="heroActions">
            <a className="button primary" href={downloads.windows}><FaWindows aria-hidden="true" /> Download for Windows <ArrowDownToLine aria-hidden="true" /></a>
            <a className="button secondary" href={downloads.linux}><FaLinux aria-hidden="true" /> Download for Linux</a>
          </div>
          <p className="heroNote">Free &amp; open source · Windows &amp; Linux. Local sync, with optional vendor accounts for cloud control.</p>
          <p className="releaseNote"><a href={releaseNotes}>New in 0.8.1: Tuya and LSC password sign-in without an Android package.</a></p>
        </div>
        <div className="heroPreview">
          <Screenshot name="plug-energy" alt="LumiSync device inventory and smart plug readings" caption="Device controls and supported plug readings." />
        </div>
      </section>

      <section className="featureSection" id="features">
        <div className="sectionHeading">
          <h2>Sync, control and<br />check your devices.</h2>
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
          <h2>Screen colors<br />across your lights.</h2>
          <p>Choose a display, map its regions to your LEDs, then adjust brightness, smoothing, saturation, and frame rate until it feels right.</p>
          <ul>
            <li>Multi-monitor selection</li>
            <li>Custom LED region mapping</li>
            <li>Groups and individual lights</li>
          </ul>
        </div>
        <Screenshot name="monitor-sync" alt="LumiSync monitor sync settings" caption="Map screen regions to compatible local lights." />
      </section>

      <section className="productSection productReverse">
        <div className="productCopy">
          <h2>Let every beat<br />set the mood.</h2>
          <p>Choose a reaction and a palette yourself, or let Auto Director follow the energy so the lighting stays alive without needing attention.</p>
          <ul>
            <li>Audio-reactive patterns</li>
            <li>Curated colour palettes</li>
            <li>Automatic scene direction</li>
          </ul>
        </div>
        <Screenshot name="music-sync" alt="LumiSync music sync settings" caption="Choose reactions, palettes and local targets." />
      </section>

      <section className="deviceSection" id="devices">
        <div className="sectionHeading compactHeading">
          <h2>Different brands.<br />One control room.</h2>
          <p>Connect over your local network, Bluetooth or your own vendor account. Screen and music sync use supported local connections; cloud connections provide manual control.</p>
        </div>
        <div className="deviceContent">
          <Screenshot name="devices" alt="Example LED strip, matrix, wall switch and smart plugs" caption="Distinct device types and controls that fit their capabilities." />
          <div>
            <table className="deviceTable">
              <caption>Supported device families</caption>
              <thead><tr><th scope="col">Family</th><th scope="col">Connection</th><th scope="col">Products</th></tr></thead>
              <tbody>{devices.map(([family, connection, products]) => (
                <tr key={family}><th scope="row">{family}</th><td>{connection}</td><td>{products}</td></tr>
              ))}</tbody>
            </table>
            <p className="supportNote">Support varies by model and firmware. iDotMatrix uses Bluetooth; supported local Tuya connections need an authorized device key.</p>
          </div>
        </div>
      </section>

      <section className="productSection" id="accounts">
        <div className="productCopy">
          <h2>Use the accounts<br />you already have.</h2>
          <p>Sign in with your Govee, Tuya Smart or LSC email and password. Choose your account country from a dropdown; LumiSync suggests your computer&apos;s region and handles account routing.</p>
          <p>Tuya and LSC password sign-in works without an Android app package or developer account. Account lists partially hide emails.</p>
          <a className="textLink" href={accountGuide}>Read account setup <ArrowRight aria-hidden="true" /></a>
        </div>
        <Screenshot name="accounts" alt="Tuya password sign-in with country selection and masked example accounts" caption="Personal sign-in, with connected accounts alongside." />
      </section>

      <section className="productSection productReverse" id="energy">
        <div className="productCopy">
          <h2>See what your<br />plug is drawing.</h2>
          <p>Open a supported Tuya or LSC smart plug to see power in watts, voltage and current. Readings refresh every five seconds while the inspector is visible.</p>
          <p>Load monthly energy totals in kWh and expand daily readings. Availability depends on what your device and account report; missing values stay marked as not reported.</p>
        </div>
        <Screenshot name="plug-energy" alt="Example smart plug with 215.1 watts, voltage, current and monthly energy use" caption="Live readings and energy history in the device inspector." />
      </section>

      <section className="download" id="download">
        <img src="/lumisync-app.png" alt="LumiSync app icon" />
        <h2>Download LumiSync 0.8.1.</h2>
        <p>Discover local devices or connect your vendor account. Prebuilt downloads include the runtime; Python is only needed for pip or source installs.</p>
        <div className="downloadGrid">
          <a className="downloadOption featured" href={downloads.windows}><FaWindows aria-hidden="true" /><span><strong>Windows</strong><small>Single-file app · x64</small></span><ArrowDownToLine aria-hidden="true" /></a>
          <a className="downloadOption" href={downloads.portable}><PackageOpen aria-hidden="true" /><span><strong>Windows portable</strong><small>Extract and run · x64</small></span><ArrowDownToLine aria-hidden="true" /></a>
          <a className="downloadOption" href={downloads.linux}><FaLinux aria-hidden="true" /><span><strong>Linux</strong><small>AppImage · x86_64</small></span><ArrowDownToLine aria-hidden="true" /></a>
          <a className="downloadOption" href={downloads.pypi}><FaPython aria-hidden="true" /><span><strong>Install with pip</strong><small>Python 3.11+ · advanced</small></span><ArrowRight aria-hidden="true" /></a>
        </div>
        <p className="requirements">Windows 10/11. Linux AppImage requires glibc 2.35+; screen sync requires X11. macOS and Wayland capture are in progress.</p>
        <p className="releaseNote"><a href={releaseNotes}>Release notes</a> · <a href={accountGuide}>Account setup</a></p>
      </section>

      <footer>
        <a className="brand" href="#top"><img src="/lumisync-mark.png" alt="" /><span>LumiSync</span></a>
        <p>A Minlor project · Screen, sound, and light in sync.</p>
        <div><a href="https://github.com/Minlor/LumiSync">GitHub</a><a href={accountGuide}>Account setup</a><a href="https://pypi.org/project/lumisync/">PyPI</a><a href="https://ko-fi.com/Minlor">Support</a><a href="https://minlor.net">minlor.net</a></div>
      </footer>
    </main>
  );
}
