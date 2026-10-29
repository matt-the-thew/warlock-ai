type Link = { label: string; href: string };

type Props = {
  links: Array<Link>;
  current: string;
};

const isActive = (href: string, current: string) =>
  href === "/" ? current === "/" : current.startsWith(href);

const Navbar = ({ links, current }: Props): React.ReactElement => {
  return (
    <header className="border-b-4 border-crimson bg-night">
      <nav className="max-w-3xl mx-auto px-4 py-3 flex items-center justify-between gap-4">
        <a href="/" className="flex items-center gap-3 no-underline">
          <img
            src="/warlock_icon.jpg"
            alt=""
            width={32}
            height={32}
            className="portrait !border-2 !shadow-none"
          />
          <span className="font-pixel text-[13px] text-ember hidden sm:inline">
            WARLOCK_AI
          </span>
        </a>
        <ul className="flex gap-4 sm:gap-6 font-pixel text-[10px]">
          {links.map((link) => {
            const active = isActive(link.href, current);
            return (
              <li key={link.href}>
                <a
                  href={link.href}
                  aria-current={active ? "page" : undefined}
                  className={`uppercase no-underline whitespace-nowrap hover:text-ember ${active ? "text-ember" : "text-bone"}`}
                >
                  {/* Inactive links keep the cursor's width on wide screens so labels don't shift. */}
                  <span
                    className={active ? "" : "hidden sm:inline invisible"}
                    aria-hidden="true"
                  >
                    ▶{" "}
                  </span>
                  {link.label}
                </a>
              </li>
            );
          })}
        </ul>
      </nav>
    </header>
  );
};

export default Navbar;
