type Props = {
  links: Array<string>;
};

const Navbar = ({ links }: Props): React.ReactElement => {
  return (
    <div className="">
      <nav className="border border-red-500 w-full h-20 py-5 border flex justify-center">
        <ul className="w-80 flex justify-evenly items-center">
          {links.map((element) => (
            <li className="decoration-0">{element}</li>
          ))}
        </ul>
      </nav>
    </div>
  );
};

export default Navbar;
